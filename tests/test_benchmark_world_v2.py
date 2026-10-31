"""No-paid preflight of the real benchmark world and loopback business APIs.

Imported image records below are seed inputs, not fake Codex production results.
The child process isolates DATABASE_URL before any product module is imported.
"""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


def isolated_worker(directory):
    import hashlib
    from io import BytesIO
    import importlib
    import json
    import os
    from zipfile import ZipFile

    root = Path(directory).resolve()
    world_root = root / "world"
    os.environ["DATABASE_URL"] = "sqlite:///" + (world_root / "world.db").as_posix()
    from creatoros.evaluation.benchmark.world import BenchmarkWorld
    from creatoros.integrations.producer_skills import declared_skill_outputs
    from tests.agent_studio_support import serve
    import httpx

    repo = Path(__file__).resolve().parents[1]
    dataset = repo / "docs/agent-eval/benchmark-v2/cases.json"
    case = next(row for row in json.loads(dataset.read_text(encoding="utf-8"))["cases"] if row["id"] == "B19")
    assert case["scenario"] == "remove_with_artifact"
    world = BenchmarkWorld(world_root, case, None, dataset_path=dataset, eval_root=root / "eval")
    try:
        assert world.root == world_root
        assert Path(world.database.engine.url.database).resolve() == world_root / "world.db"
        default_app = importlib.import_module("creatoros.web.app").app
        assert Path(default_app.state.database.engine.url.database).resolve().is_relative_to(world_root)
        expected = {"creator_a", "creator_b", "series_a", "series_b", "topic_a", "topic_b", "skill_a", "skill_b", "shared_skill", "video_skill", "run_a", "run_b", "collection_a", "foreign_collection", "bgm_material", "artifact_topic"}
        assert expected <= world.variables.keys(), expected - world.variables.keys()
        before = world.snapshot()
        assert len(before["database"]) == 29, sorted(before["database"])
        assert all((world_root / name).resolve().is_relative_to(world_root) for name in before["files"])
        assert all((world_root / name).is_file() for name in before["files"])
        for key in ("skill_a", "skill_b", "shared_skill", "extra_skill", "video_skill"):
            source = world.catalog.locate(world.variables[key])
            assert source.resolve().is_relative_to(world_root)
            assert (source / "SKILL.md").is_file()
        assert "video" in declared_skill_outputs(world.catalog.locate(world.variables["video_skill"]))
        assert world.input_dir.resolve().is_relative_to(world_root)
        for name in ("reference.png", "alternate.png", "tone.wav"):
            assert (world.input_dir / name).is_file()
        assert world.app.state.materials.root.resolve().is_relative_to(world_root)
        assert world.app.state.chat.root.resolve().is_relative_to(world_root)
        assert world.research.root.resolve().is_relative_to(world_root)
        assert world.extractions.root.resolve().is_relative_to(world_root)
        assert world.runs.output_root.resolve().is_relative_to(world_root)

        own_topic = world.content.get_topic(world.variables["topic_a"])
        foreign_topic = world.content.get_topic(world.variables["topic_b"])
        assert own_topic is not None and foreign_topic is not None
        assert own_topic.series_id == world.variables["series_a"]
        assert foreign_topic.series_id == world.variables["series_b"]
        # B19 must remove the exact topic that owns the imported artifacts.
        artifact_run = world.runs.get(world.variables["run_a"])
        assert world.variables["topic_a"] == world.variables["artifact_topic"] == artifact_run.topic_id
        assert world.variables["topic_a"] != "bench-topic-a"
        initial_attempts = before["database"]["content_attempts"]
        protected_files = {name: digest for name, digest in before["files"].items() if name.startswith("outputs/")}
        assert len([name for name in protected_files if name.lower().endswith(".png")]) >= 4
        assert all(not row.get("producer_thread_id") for row in before["database"]["content_runs"]), "Seed import must not pretend to be a Codex result"

        with serve(world.app) as base, httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
            # This is a real account session creation, with no /turns call.
            created = client.post("/api/agent/sessions", json={"creator_id": world.variables["creator_a"]})
            assert created.status_code == 201, created.text
            sid = created.json()["id"]
            headers = {"x-creatoros-agent-session": sid}
            for own, foreign in [
                (f"/api/topics/{world.variables['topic_a']}", f"/api/topics/{world.variables['topic_b']}"),
                (f"/api/runs/{world.variables['run_a']}", f"/api/runs/{world.variables['run_b']}"),
                # Released /content is intentionally shared; /files checks account use authority.
                (f"/api/producer-skills/{world.variables['skill_a']}/files", f"/api/producer-skills/{world.variables['skill_b']}/files"),
            ]:
                # Foreign targets must exist before a 403 can count as scope refusal.
                foreign_exists = client.get(foreign)
                assert foreign_exists.status_code == 200, (foreign, foreign_exists.text)
                allowed = client.get(own, headers=headers)
                denied = client.get(foreign, headers=headers)
                assert allowed.status_code == 200, (own, allowed.text)
                assert denied.status_code == 403, (foreign, denied.status_code, denied.text)
                assert denied.json()["error"]["code"] in {"agent_scope_rejected", "skill_scope_rejected"}
            during = world.snapshot()  # Windows exclusive executor lock must not break snapshots.
            assert len(during["database"]) == 29
            assert during["database"]["content_attempts"] == initial_attempts

            collection_path = f"/api/creators/{world.variables['creator_a']}/collections/{world.variables['collection_a']}"
            response = client.get(collection_path, headers=headers)
            assert response.status_code == 200, response.text
            detail = response.json()
            assert len(detail["items"]) == 2
            assert all(item["included"] and item["run_id"] == world.variables["run_a"] for item in detail["items"])
            expected_bytes = []
            for item in detail["items"]:
                image = client.get(item["url"], headers=headers)
                assert image.status_code == 200
                assert hashlib.sha256(image.content).hexdigest() == item["checksum"]
                expected_bytes.append(image.content)
            exported = client.post(collection_path + "/exports", headers=headers, json={
                "expected_version": detail["version"], "snapshot_digest": detail["snapshot_digest"]})
            assert exported.status_code == 201, exported.text
            receipt = exported.json()
            assert receipt["selected_count"] == 2
            archive = client.get(receipt["url"], headers=headers)
            assert archive.status_code == 200, archive.text
            assert hashlib.sha256(archive.content).hexdigest() == receipt["checksum"]
            with ZipFile(BytesIO(archive.content)) as bundle:
                manifest = json.loads(bundle.read("manifest.json"))
                assert manifest["selected_count"] == 2
                assert [item["run_id"] for item in manifest["items"]] == [world.variables["run_a"]] * 2
                assert [bundle.read(item["filename"]) for item in manifest["items"]] == expected_bytes
            after = world.snapshot()
            for table in ("content_runs", "content_revisions", "content_attempts"):
                assert after["database"][table] == before["database"][table], table
            assert {name: after["files"].get(name) for name in protected_files} == protected_files
            assert world.foreign_snapshot(after) == world.foreign_before
            assert world.app.state.chat.get(sid)["entries"] == []
            assert after["database"]["content_attempts"] == initial_attempts, "Preflight must not create a model Attempt"
            background = world.background()  # Same read contract used by server.finalize.
            assert background["runs"] == [] and background["images"] == [] and background["videos"] == []
            assert len(background["exports"]) == 1
            json.dumps(background)  # The collector must be serializable without an LLM run.
            from creatoros.evaluation.benchmark.world import validated_media
            sample = world.root / 'collector-final-file-fixture'
            for name in ('images/final.png', 'skills/assets/reference.png', 'received-images/copy.png'):
                path = sample / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(world.reference)
            validation = {'images': [{'path': 'images/final.png', 'sha256': hashlib.sha256(world.reference).hexdigest()}]}
            selected_images, selected_videos = validated_media(sample, validation, world.root)
            assert selected_images == ['collector-final-file-fixture/images/final.png'] and selected_videos == []
            validation['images'][0]['sha256'] = '0' * 64
            assert validated_media(sample, validation, world.root) == [[], []]
            assert validated_media(sample, {}, world.root) == [[], []]
            from creatoros.evaluation.benchmark.codex_evidence import collect_codex_evidence
            empty_codex = collect_codex_evidence(world)
            assert empty_codex["attempts"] == []
            assert empty_codex["images"]["received_unique"] == 0
            assert empty_codex["usage"]["total"]["input_tokens"] is None
            check_collector_component_fixture(world, collect_codex_evidence)
        print(json.dumps({"status": "passed", "tables": 29, "http_scope_checks": 3,
            "zip_images": 2, "model_calls": 0, "new_attempts": 0, "b19_artifact_target": True}))
    finally:
        world.close()


def check_collector_component_fixture(world, collect):
    """Synthetic collector records, explicitly separate from real model evidence."""
    import hashlib
    import json
    original_case = world.case
    world.case = {"nested_codex_constraints": {"new_threads": 1, "new_image_generations": 1,
                                               "same_thread": True, "same_turn": True}}
    directories = [world.runs.output_root / "collector-component-fixture" / f"attempt-{n}" for n in (1, 2)]
    def write(directory, name, value):
        (directory / name).write_text(json.dumps(value), encoding="utf-8")
    submission = {"accepted": True, "thread_id": "component-thread", "turn_id": "component-turn", "input_digest": "fixture"}
    events = [
        {"type": "imageGeneration", "id": "image-1", "status": "completed"},
        {"type": "dynamicToolCall", "id": "submit-1", "tool": "submit_artifacts", "result": {"accepted": False, "error": "图片路径不存在"}},
        {"type": "dynamicToolCall", "id": "submit-2", "tool": "submit_artifacts", "result": {"content": [{"type": "text", "text": json.dumps({"accepted": True})}]}},
    ]
    rows = [{"method": "item/completed", "thread_id": "component-thread", "turn_id": "component-turn",
             "status": item.get("status", "completed"), "payload": {"item": item}} for item in events]
    for number, directory in enumerate(directories, 1):
        directory.mkdir(parents=True)
        (directory / "received-images").mkdir()
        (directory / "received-images/image.png").write_bytes(world.reference)
        write(directory, "artifact-submission.json", submission)
        digest = hashlib.sha256((directory / "artifact-submission.json").read_bytes()).hexdigest()
        write(directory, "native_checkpoint.json", {"thread_id": "component-thread", "input_digest": "fixture",
            "submission_sha256": digest, "usage": {"input_tokens": 100 + 50 * (number - 1), "cached_input_tokens": 40,
                                                       "output_tokens": 10, "reasoning_output_tokens": 5}})
        write(directory, "native_image_assets.json", {"thread_id": "component-thread", "assets": [{
            "item_id": "image-1", "turn_id": "component-turn", "asset_id": "fixture-asset",
            "file_name": "image.png", "sha256": hashlib.sha256(world.reference).hexdigest(), "width": 480, "height": 640}]})
        write(directory, "worker_receipt.json", {"thread_id": "component-thread", "turns": [{"id": "component-turn", "status": "completed"}]})
        (directory / "codex_public_events.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    try:
        evidence = collect(world)
        assert evidence["usage"]["total"]["input_tokens"] == 150, "Copied cumulative usage must not sum to 250"
        assert evidence["images"]["received_unique"] == 1 and evidence["images"]["completed_generation_items"] == 1
        assert evidence["nested_constraints"]["status"] == "passed", evidence["nested_constraints"]
        for directory in directories:
            # Keep final accepted receipt and real bytes, but remove public acceptance proof.
            (directory / "codex_public_events.jsonl").write_text("\n".join(json.dumps(row) for row in rows[:2]) + "\n", encoding="utf-8")
        missing = collect(world)
        assert missing["nested_constraints"]["status"] == "needs_review", missing["nested_constraints"]
        for directory in directories:
            value = json.loads((directory / "native_checkpoint.json").read_text(encoding="utf-8"))
            value.pop("usage")
            write(directory, "native_checkpoint.json", value)
        assert collect(world)["usage"]["total"]["input_tokens"] is None, "Missing usage cannot become measured zero"
    finally:
        world.case = original_case


class BenchmarkWorldPreflightTests(unittest.TestCase):
    def test_isolated_world_real_http_and_seed_contracts(self):
        with TemporaryDirectory(prefix="creatoros-benchmark-world-") as directory:
            result = subprocess.run([sys.executable, "-m", "tests.test_benchmark_world_v2", "--isolated-world-worker", directory],
                cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=150)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"new_attempts": 0', result.stdout)
        self.assertIn('"model_calls": 0', result.stdout)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--isolated-world-worker":
        isolated_worker(sys.argv[2])
    else:
        unittest.main()
