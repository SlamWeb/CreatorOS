"""Real local ZIP bytes and browser metadata oracles; no models or production writes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZIP_STORED, ZipFile

from creatoros.evaluation.benchmark.grader import MissingEvidence, _expectation
from creatoros.evaluation.benchmark.world import archive_evidence


class BenchmarkMediaOraclesTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.raw = [b"first-real-member-bytes", b"second-real-member-bytes"]
        self.items = [{"run_id": "R1", "revision_id": "V1", "order": index,
            "checksum": hashlib.sha256(raw).hexdigest(), "filename": f"{index:04}.png"}
            for index, raw in enumerate(self.raw, 1)]
        self.rule = {"kind": "zip_contents", "source": "initial_collection", "collection_id": "C", "count": 2}

    def captured(self, items=None, raw=None):
        items = deepcopy(self.items if items is None else items)
        path = self.root / "export.zip"
        with ZipFile(path, "w", compression=ZIP_STORED) as bundle:
            for item, body in zip(items, self.raw if raw is None else raw):
                bundle.writestr(item["filename"], body)
            bundle.writestr("manifest.json", json.dumps({"collection_id":"C", "selected_count":len(items), "items":items}))
        return archive_evidence(path, self.root)

    def evidence(self, archive):
        return {"background": {"export_contents": [archive],
            "initial_collections": {"C": {"items": self.items}}, "collections": {"C": {"items": self.items}}}}

    def test_initial_collection_matches_actual_bytes_and_original_order(self):
        archive = self.captured()
        self.assertEqual(archive["errors"], [])
        self.assertEqual([x["sha256"] for x in archive["entries"]], [x["checksum"] for x in self.items])
        self.assertTrue(_expectation(self.rule, self.evidence(archive))[0])

    def test_initial_collection_reordered_zip_fails_even_if_checksums_are_valid(self):
        archive = self.captured(list(reversed(self.items)), list(reversed(self.raw)))
        self.assertFalse(_expectation(self.rule, self.evidence(archive))[0])

    def test_zip_payload_tampering_and_extra_images_fail(self):
        archive = self.captured(raw=[b"different-content", self.raw[1]])
        self.assertFalse(_expectation(self.rule, self.evidence(archive))[0])
        third = {**self.items[0], "filename":"0003.png", "order":3}
        archive = self.captured(self.items+[third], self.raw+[self.raw[0]])
        self.assertFalse(_expectation(self.rule, self.evidence(archive))[0])

    def test_new_runs_accept_actual_collection_order_without_prescribing_story_order(self):
        items = deepcopy(self.items)
        items[1].update(run_id="R2", revision_id="V2", order=1)
        archive = self.captured(list(reversed(items)), list(reversed(self.raw)))
        data = self.evidence(archive)
        data["background"]["collections"]["C"]["items"] = list(reversed(items))
        data["before"] = {"database":{"content_runs":[{"id":"OLD"}]}}
        data["after"] = {"database":{
            "content_runs":[{"id":"OLD"}, {"id":"R1","topic_id":"T","active_revision_number":1},
                {"id":"R2","topic_id":"T","active_revision_number":1}],
            "content_revisions":[{"id":item["revision_id"],"content_run_id":item["run_id"],"revision_number":1,
                "validation_json":{"images":[{"order":item["order"],"sha256":item["checksum"]}]}} for item in items]}}
        rule = {"kind":"zip_contents","source":"new_runs","topic_id":"T","count":2}
        self.assertTrue(_expectation(rule, data)[0])
        # An old Run with identical bytes is still not this batch's image identity.
        data["background"]["export_contents"][0]["manifest"]["items"][0]["run_id"] = "OLD"
        self.assertFalse(_expectation(rule, data)[0])

    def test_missing_zip_capture_is_unknown_instead_of_guessed(self):
        with self.assertRaises(MissingEvidence):
            _expectation(self.rule, {"background":{}})

    def test_invalid_and_outside_zip_are_reported_without_extraction(self):
        bad = self.root / "bad.zip"
        bad.write_bytes(b"not a ZIP")
        self.assertTrue(archive_evidence(bad, self.root)["errors"])
        outside = self.root.parent / "this-file-must-not-be-read.zip"
        captured = archive_evidence(outside, self.root)
        self.assertTrue(captured["errors"])
        self.assertIsNone(captured["manifest"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["bad.zip"])

    def test_archive_traversal_member_is_rejected_not_extracted(self):
        path = self.root / "unsafe.zip"
        with ZipFile(path, "w") as bundle:
            bundle.writestr("../outside.png", b"must stay inside ZIP")
            bundle.writestr("manifest.json", "{}")
        self.assertTrue(archive_evidence(path, self.root)["errors"])
        self.assertFalse((self.root.parent / "outside.png").exists())

    def test_video_duration_uses_loaded_browser_metadata(self):
        rule = {"kind":"video_duration","min_seconds":1.5,"max_seconds":2.5}
        data = {"browser":{"production_pages":[{"video":{"width":480,"height":640,"duration":2.02}}]}}
        self.assertTrue(_expectation(rule, data)[0])
        data["browser"]["production_pages"][0]["video"]["duration"] = 20
        self.assertFalse(_expectation(rule, data)[0])
        data["browser"]["production_pages"][0]["video"]["duration"] = 2
        data["browser"]["production_pages"][0]["video"]["width"] = 0
        self.assertFalse(_expectation(rule, data)[0])
        with self.assertRaises(MissingEvidence):
            _expectation(rule, {})

    def test_account_library_addition_must_not_drop_previous_skills(self):
        def state(ids):
            return {"database":{"creator_skill_refs":[{"creator_id":"A","skill_ids_json":json.dumps(ids)}]}}
        data = {"variables":{"creator_a":"A"},"before":state(["SK1","SK2"]),"after":state(["SK1","SK2","NEW"])}
        self.assertTrue(_expectation({"kind":"library_preserved"}, data)[0])
        data["after"] = state(["NEW"])
        self.assertFalse(_expectation({"kind":"library_preserved"}, data)[0])


if __name__ == "__main__":
    unittest.main()
