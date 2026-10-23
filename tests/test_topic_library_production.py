"""Isolated SQLite/HTTP and local artifact fixtures; never real model production."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, Series, TopicSource
from tests.smoke_topic_research import seed_batch
from tests.studio_review_fixtures import make_fixture


class TopicProductionViewTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db, self.runs, self.producer, self.app = make_fixture(self.root)
        self.addCleanup(self.db.close)
        self.catalog = self.app.state.skill_installs.catalog
        self.repo = ContentRepository(self.db)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def library(self, state="all", **params):
        response = self.client.get("/api/series/agent-notes/topic-library", params={"state": state, **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def make_skill(self, name="example", role="legacy_end_to_end"):
        directory = self.root / name
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: isolated example\ncreatoros-display-name: 示例{name}\n"
            "creatoros-output: social-content-pack.image-carousel\n---\nUse examples.\n", encoding="utf-8")
        return self.catalog.register_local(directory, role=role)

    def bind(self, skill=None, *, mind=None, production=None):
        with self.db.session() as session:
            series = session.get(Series, "agent-notes")
            series.skill_name = skill["id"] if skill else None
            series.mind_skill_id = mind["id"] if mind else None
            series.production_skill_id = production["id"] if production else None

    def test_production_filter_runs_before_pagination_and_retains_legacy_selection_filters(self):
        for i in range(25):
            self.repo.add_topic(topic_id=f"extra-{i}", series_id="agent-notes", title=f"Extra {i}", source=TopicSource.MANUAL)
        seed_batch(self.app.state.topic_research, "agent-notes")
        run = self.runs.create("review-1")
        self.runs.execute(run.id)
        produced = self.library("produced")
        self.assertEqual([row["id"] for row in produced["items"]], ["review-1"])
        first, second = self.library("unproduced"), self.library("unproduced", offset=20)
        self.assertEqual(first["page"]["total"], 28)
        self.assertEqual([len(first["items"]), len(second["items"])], [20, 8])
        ids = [row["id"] for row in first["items"] + second["items"]]
        self.assertEqual(len(set(ids)), 28)
        self.assertNotIn("review-1", ids)
        self.assertEqual(self.library("pending")["page"]["total"], 2)
        self.assertEqual(self.library("queued")["page"]["total"], 27)
        self.assertEqual(self.client.get("/api/series/agent-notes/topic-library?state=bad").status_code, 422)

    def test_existing_run_keeps_v1_while_unstarted_topic_previews_column_v2(self):
        first = self.make_skill()
        self.bind(first)
        run = self.runs.create("review-2")
        before = self.runs.get(run.id).input_snapshot_json.copy()
        candidate = self.catalog.create_skill_candidate(first["id"], first["digest"], "isolated-v2")
        second = self.catalog.publish_skill_candidate(candidate["id"], candidate["digest"])
        self.bind(second)
        # Reading labels must not hash/repair assets or enumerate the whole library.
        with patch("creatoros.integrations.producer_skills._digest", side_effect=AssertionError("unexpected asset hash")):
            result = {row["id"]: row for row in self.library()["items"]}
        planned, task = result["review-1"]["skill_usage"], result["review-2"]["skill_usage"]
        self.assertEqual((planned["source"], planned["items"][0]["id"], planned["items"][0]["version_number"]),
                         ("series", second["id"], 2))
        self.assertEqual((task["source"], task["items"][0]["id"], task["items"][0]["version_number"]),
                         ("run", first["id"], 1))
        self.assertEqual(self.runs.get(run.id).input_snapshot_json, before)
        self.assertNotIn("local_path", repr(result))
        self.assertNotIn("skill_digest", repr(result))

    def test_two_skill_task_uses_both_original_ids_after_column_changes(self):
        mind = self.make_skill("mind", "mind")
        visual = self.make_skill("visual", "production")
        self.bind(mind=mind, production=visual)
        native = ContentRunService(self.db, output_root=self.runs.output_root, production_protocol="native-v1")
        native.create("review-2")
        candidate = self.catalog.create_skill_candidate(visual["id"], visual["digest"], "visual-v2")
        second = self.catalog.publish_skill_candidate(candidate["id"], candidate["digest"])
        self.bind(mind=mind, production=second)
        result = {row["id"]: row for row in self.library()["items"]}
        self.assertEqual([s["id"] for s in result["review-2"]["skill_usage"]["items"]], [mind["id"], visual["id"]])
        self.assertEqual([s["version_number"] for s in result["review-1"]["skill_usage"]["items"]], [1, 2])

    def test_rework_without_new_delivery_is_unproduced_but_keeps_previous_artifacts(self):
        run = self.runs.create("review-1")
        self.runs.execute(run.id)
        old = self.runs.get_active_revision(run.id)
        before = {p: p.read_bytes() for p in Path(old.artifact_directory).rglob("*") if p.is_file()}
        current = self.runs.get(run.id)
        response = self.client.post(f"/api/runs/{run.id}/revisions",
            json={"instruction": "isolated small change", "expected_version": current.version})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.library("produced")["items"], [])
        row = next(t for t in self.library("unproduced")["items"] if t["id"] == "review-1")
        self.assertIsNone(row["cover_url"])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(self.producer.calls, 1)

    def test_missing_cover_keeps_recorded_production_and_reads_never_repair_or_generate(self):
        run = self.runs.create("review-1")
        self.runs.execute(run.id)
        directory = Path(self.runs.get_active_revision(run.id).artifact_directory)
        (directory / "images/01.png").rename(directory / "images/01.missing")
        before = self.runs.get(run.id)
        row = self.library("produced")["items"][0]
        self.assertEqual(row["id"], "review-1")
        self.assertIsNone(row["cover_url"])
        self.assertEqual(self.runs.get(run.id).version, before.version)
        self.assertEqual(self.producer.calls, 1)

    def test_metadata_is_deduplicated_and_reading_does_not_restore_a_missing_working_tree(self):
        skill = self.make_skill()
        self.bind(skill)
        working = Path(skill["local_path"])
        working.rename(working.with_name(working.name + "-held"))
        with patch.object(self.catalog, "reference_metadata", wraps=self.catalog.reference_metadata) as metadata:
            rows = self.library()["items"]
            self.assertEqual(metadata.call_count, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["skill_usage"]["items"][0]["version_number"], 1)
        self.assertFalse(working.exists())


if __name__ == "__main__":
    unittest.main()
