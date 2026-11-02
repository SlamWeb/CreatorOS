"""Exact one-off reference Skills: isolated HTTP/SQLite/files, no model calls."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from sqlalchemy import func, select

from creatoros.ai.types import ToolCall
from creatoros.context import RuntimeContext
from creatoros.runs import ContentRunError
from creatoros.storage import ContentRepository, ContentRun, PendingOperation, Series, Topic, WriteReceipt
from creatoros.storage.models import ProductionQueueItem
from creatoros.tools import execute_tool_call
from creatoros.tools.production_arguments import StartRunArgs
from creatoros.web.app import create_app
from tests.agent_studio_support import serve
from tests import test_reference_production_api as fixture


class ReferenceSelectionTests(unittest.TestCase):
    tearDown = fixture.ReferenceProductionAPITests.tearDown
    service = fixture.ReferenceProductionAPITests.service
    upload = fixture.ReferenceProductionAPITests.upload
    freeze = fixture.ReferenceProductionAPITests.freeze

    def setUp(self):
        fixture.ReferenceProductionAPITests.setUp(self)
        self.producer = fixture.CapturingProducer(block=True)
        self.runs = self.service(self.producer)
        self.app = create_app(database=self.database, run_service=self.runs)
        self.library = self.app.state.skill_library
        self.catalog = self.library.catalog
        self.skills = []
        for name in ("reference-one", "reference-two", "reference-foreign"):
            directory = self.root / name
            directory.mkdir()
            (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: reference selection fixture\n---\n"
                                               f"Exact original rules for {name}.\n", encoding="utf-8")
            self.skills.append(self.catalog.register_local(directory, role="production"))
        repo = ContentRepository(self.database)
        repo.create_creator(creator_id="foreign", display_name="Foreign")
        self.library.add("foreign", self.skills[2]["id"], "foreign-reference-library")
        for item in self.skills[:2]:
            self.library.add("creator", item["id"], "reference-library-" + item["id"])
        with self.database.session() as session:
            series = session.get(Series, "series")
            series.skill_name = series.mind_skill_id = series.production_skill_id = None
        self.pool = self.library.set_series("series", 0, [item["id"] for item in self.skills[:2]], {"image": [], "video": []})

    def counts(self):
        with self.database.session() as session:
            return tuple(session.scalar(select(func.count()).select_from(model)) for model in
                         (Topic, ContentRun, ProductionQueueItem, PendingOperation, WriteReceipt))

    def body(self, client, skill_ids=None, request_id="reference-selected-once"):
        image, original = self.upload(client)
        ref = self.freeze(client, [image], instruction="完整保留我的原始要求。", presentation=True)
        return {"reference_id": ref, "title": "参考图一次性选择", "request_id": request_id,
                "skill_ids": skill_ids if skill_ids is not None else [item["id"] for item in self.skills[:2]]}, original

    def test_web_pool_without_default_accepts_multiple_exact_versions_and_freezes_replay(self):
        with serve(self.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            body, raw = self.body(client)
            baseline = self.counts()
            try:
                accepted = client.post("/api/series/series/reference-production", json=body)
                self.assertEqual(accepted.status_code, 202, accepted.text)
                run = accepted.json()
                self.assertTrue(self.producer.started.wait(5))
                selected = self.runs.get(run["id"]).input_snapshot_json["execution_spec"]["skills"]
                self.assertEqual([item["skill_id"] for item in selected], body["skill_ids"])
                self.assertEqual([item["role"] for item in selected], ["selected", "selected"])
                for saved, original in zip(selected, self.skills):
                    self.assertEqual(saved["digest"], original["digest"])
                    self.assertTrue(Path(saved["path"], "SKILL.md").is_file())
                    self.assertNotEqual(Path(saved["path"]), Path(original["local_path"]))
                supplied = self.producer.requests[0]["task_references"]
                self.assertEqual(supplied["instructions"], "完整保留我的原始要求。")
                self.assertTrue(supplied["reference_presentation"])
                self.assertEqual(Path(supplied["images"][0]["path"]).read_bytes(), raw)
                # The response and frozen directory remain authoritative even if
                # the catalog bytes later change; replay must not re-resolve them.
                source = Path(self.skills[0]["local_path"], "SKILL.md")
                source.write_text(source.read_text(encoding="utf-8") + "\nChanged after acceptance", encoding="utf-8")
                replay = client.post("/api/series/series/reference-production", json=body)
                self.assertEqual(replay.status_code, 202, replay.text)
                self.assertEqual(replay.json()["id"], run["id"])
                self.assertIn("Exact original rules", Path(selected[0]["path"], "SKILL.md").read_text(encoding="utf-8"))
                self.assertEqual(self.counts(), tuple(value + 1 for value in baseline))
                self.assertEqual(self.library.get_series("series")["defaults"], self.pool["defaults"])
                with self.database.session() as session:
                    series = session.get(Series, "series")
                    self.assertEqual((series.skill_name, series.mind_skill_id, series.production_skill_id), (None, None, None))
            finally:
                self.producer.release.set()

    def test_same_request_changed_selection_conflicts_and_invalid_selections_leave_no_rows(self):
        with serve(self.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            body, _ = self.body(client, [self.skills[0]["id"]])
            baseline = self.counts()
            for selection in ([], [self.skills[0]["id"]] * 2, ["missing-skill"], [self.skills[2]["id"]]):
                response = client.post("/api/series/series/reference-production", json={**body, "skill_ids": selection})
                self.assertIn(response.status_code, (403, 422), response.text)
                self.assertEqual(self.counts(), baseline)
            try:
                first = client.post("/api/series/series/reference-production", json=body)
                self.assertEqual(first.status_code, 202, first.text)
                for changed in ([self.skills[1]["id"]], None):
                    altered = {**body, "skill_ids": changed}
                    response = client.post("/api/series/series/reference-production", json=altered)
                    self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.counts(), tuple(value + 1 for value in baseline))
            finally:
                self.producer.release.set()

    def test_snapshot_and_queue_failures_rollback_topic_run_and_receipt(self):
        with serve(self.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            body, _ = self.body(client)
            baseline = self.counts()
            with patch("creatoros.runs.service.freeze_execution_spec", side_effect=ValueError("Injected snapshot failure")):
                response = client.post("/api/series/series/reference-production", json=body)
            self.assertGreaterEqual(response.status_code, 400)
            self.assertLess(response.status_code, 500)
            self.assertEqual(self.counts(), baseline)
            with patch.object(self.app.state.executor.queue, "enqueue", side_effect=ContentRunError("Injected queue failure")):
                response = client.post("/api/series/series/reference-production", json=body)
            self.assertGreaterEqual(response.status_code, 400)
            self.assertLess(response.status_code, 500)
            self.assertEqual(self.counts(), baseline)
            self.assertFalse(self.producer.started.is_set())

    def test_concurrent_http_replay_creates_only_one_topic_run_and_queue(self):
        with serve(self.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            body, _ = self.body(client)
            baseline = self.counts()
            def submit(_):
                with httpx.Client(base_url=base, trust_env=False, timeout=20) as connection:
                    response = connection.post("/api/series/series/reference-production", json=body)
                    self.assertEqual(response.status_code, 202, response.text)
                    return response.json()["id"]
            try:
                with ThreadPoolExecutor(max_workers=4) as pool:
                    ids = list(pool.map(submit, range(4)))
                self.assertEqual(len(set(ids)), 1)
                self.assertEqual(self.counts(), tuple(value + 1 for value in baseline))
                self.assertTrue(self.producer.started.wait(5))
                self.assertEqual(len(self.producer.calls), 1)
            finally:
                self.producer.release.set()

    def test_reference_action_schema_and_tool_forward_selection_without_a_new_identity(self):
        ids = [item["id"] for item in self.skills[:2]]
        with serve(self.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            sid = client.post("/api/agent/sessions", json={"creator_id": "creator"}).json()["id"]
            upload = client.post(f"/api/agent/sessions/{sid}/attachments", json={"name": "ref.png",
                "data_base64": base64.b64encode(fixture.picture("blue")).decode()}).json()
            arguments = {"action": "reference", "series_id": "series", "title": "Agent 本次 Skill",
                         "attachment_ids": [upload["id"]], "instructions": "原图和完整要求", "skill_ids": ids}
            StartRunArgs.model_validate(arguments)
            context = RuntimeContext(self.root, creator_id="creator", agent_session_id=sid,
                                     studio_url=base, user_request_id="reference-selection-turn")
            try:
                results = [execute_tool_call(ToolCall(f"ref-{n}", "start_content_run", json.dumps(arguments)), context,
                                             model_requested=True) for n in range(2)]
                self.assertTrue(all(not result.is_error for result in results), [result.content for result in results])
                run_ids = [json.loads(result.content)["run_id"] for result in results]
                self.assertEqual(run_ids[0], run_ids[1])
                changed = execute_tool_call(ToolCall("ref-changed", "start_content_run", json.dumps({**arguments,
                    "skill_ids": [ids[1]]})), context, model_requested=True)
                self.assertTrue(changed.is_error, changed.content)
                self.assertEqual(json.loads(changed.content)["error"], "idempotency_conflict")
                self.assertEqual([item["skill_id"] for item in self.runs.get(run_ids[0]).input_snapshot_json["execution_spec"]["skills"]], ids)
            finally:
                self.producer.release.set()

    def test_old_unmarked_reference_receipt_replays_without_new_selection(self):
        self.library.add("creator", "knowledge-to-carousel", "legacy-reference-default")
        self.library.set_series("series", self.pool["version"], ["knowledge-to-carousel"],
            {"image": [{"skill_id": "knowledge-to-carousel", "role": "primary"}], "video": []})
        with serve(self.app) as base, httpx.Client(base_url=base, trust_env=False, timeout=20) as client:
            body, _ = self.body(client)
            body.pop("skill_ids")
            try:
                accepted = client.post("/api/series/series/reference-production", json=body)
                self.assertEqual(accepted.status_code, 202, accepted.text)
                with self.database.session() as session:
                    receipt = session.get(WriteReceipt, body["request_id"])
                    old_value = dict(receipt.response_json)
                    old_value.pop("reference_production")
                    receipt.response_json = old_value
                baseline = self.counts()
                replay = client.post("/api/series/series/reference-production", json=body)
                self.assertEqual(replay.status_code, 202, replay.text)
                self.assertEqual(replay.json()["id"], accepted.json()["id"])
                self.assertEqual(self.counts(), baseline)
                with self.database.session() as session:
                    self.assertEqual(session.get(WriteReceipt, body["request_id"]).response_json, old_value)
            finally:
                self.producer.release.set()


if __name__ == "__main__":
    unittest.main()
