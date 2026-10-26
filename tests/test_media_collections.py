"""Real isolated SQLite/HTTP checks of reference-only image management.

ReviewProducer creates deterministic local image fixtures only. These checks do
not call a model or establish real Codex generation quality.
"""
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event, inspect, text

from creatoros.runs import ContentRunService
from creatoros.storage import (
    ContentRepository, ContentRevision, Database, Series,
    TopicRemoval, TopicSource, upgrade_database,
)
from creatoros.storage.models import Base
from tests.studio_review_fixtures import make_fixture
from tests.test_topic_edit_migration import config


def business_snapshot(database):
    """Compare exact original rows, ignoring only the new collection table."""
    with database.engine.connect() as connection:
        return {table: sorted([dict(row) for row in connection.execute(
            text(f'SELECT * FROM "{table}"')).mappings()], key=repr)
            for table in inspect(connection).get_table_names()
            if table not in {"media_collections", "alembic_version"}}


class MediaCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db, self.runs, self.producer, self.app = make_fixture(self.root)
        self.addCleanup(self.db.close)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.base = "/api/creators/review-lab/collections"
        self.first = self.produce("review-1", 2)
        self.extra = self.produce("review-2", 1)

    def produce(self, topic, count):
        self.producer.count = count
        run = self.runs.create(topic)
        self.runs.execute(run.id)
        return self.runs.get(run.id)

    def create(self, **changes):
        payload = dict(name="素材测试", request_id="collection-request", run_ids=[self.first.id])
        payload.update(changes)
        response = self.client.post(self.base, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def patch(self, collection, **changes):
        response = self.client.patch(f"{self.base}/{collection['id']}", json={
            "expected_version": collection["version"], **changes})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def detail(self, collection):
        response = self.client.get(f"{self.base}/{collection['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def download(self, collection):
        return self.client.get(f"{self.base}/{collection['id']}/download", params={
            "version": collection["version"], "snapshot": collection["snapshot_digest"]})

    def original_files(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in (self.root / "outputs").rglob("*") if path.is_file()}

    def test_multi_run_revisions_automatic_topic_extra_run_and_read_only_get(self):
        self.runs.request_revision(self.first.id, "Local second fixture revision",
            expected_version=self.runs.get(self.first.id).version)
        self.producer.count = 3
        self.runs.execute(self.first.id)
        fresh = self.runs.restart_fresh(self.first.id,
            expected_version=self.runs.get(self.first.id).version, request_id="fresh-fixture")
        self.producer.count = 1
        self.runs.execute(fresh.id)
        before, files, calls = business_snapshot(self.db), self.original_files(), self.producer.calls
        collection = self.create(topic_id="review-1", run_ids=[self.extra.id])
        self.assertEqual(len(collection["items"]), 7)
        self.assertEqual({item["run_id"] for item in collection["items"]},
                         {self.first.id, fresh.id, self.extra.id})
        self.assertEqual({item["revision_number"] for item in collection["items"]
                          if item["run_id"] == self.first.id}, {1, 2})
        self.assertEqual(len({item["id"] for item in collection["items"]}), 7)
        listed = self.client.get(self.base).json()
        self.assertIn(collection["id"], [item["id"] for item in listed["items"]])
        sources = self.client.get(self.base + "/sources", params={"topic_id": "review-1"})
        self.assertEqual(sources.status_code, 200, sources.text)
        self.assertEqual({row["run_id"] for row in sources.json()["items"]}, {self.first.id, fresh.id})
        self.assertEqual(self.detail(collection), collection)
        for item in collection["items"]:
            response = self.client.get(item["url"])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["content-type"], "image/png")
            self.assertEqual(hashlib.sha256(response.content).hexdigest(), item["checksum"])
        self.assertEqual(business_snapshot(self.db), before)
        self.assertEqual(self.original_files(), files)
        self.assertEqual(self.producer.calls, calls)

    def test_waiting_source_finishes_and_new_run_appends_after_saved_order(self):
        pending = self.runs.restart_fresh(self.first.id,
            expected_version=self.first.version, request_id="pending-fixture")
        collection = self.create(topic_id="review-1", run_ids=[self.extra.id])
        order = [item["id"] for item in reversed(collection["items"])]
        collection = self.patch(collection, order_ids=order, excluded_ids=[order[0]])
        self.assertTrue(any(row["run_id"] == pending.id and row["image_count"] == 0
                            for row in collection["sources"]))
        before, calls = business_snapshot(self.db), self.producer.calls
        self.assertEqual(self.detail(collection), collection)
        self.assertEqual(business_snapshot(self.db), before)
        self.assertEqual(self.producer.calls, calls)
        self.producer.count = 2
        # Hold only the local fixture producer to inspect a real producing Run.
        # No model is involved and collection reads must never execute this work.
        started, release = Event(), Event()
        original_produce = self.producer.produce_to

        def hold_fixture(**request):
            started.set()
            if not release.wait(20):
                raise RuntimeError("Local collection test did not release its fixture")
            return original_produce(**request)

        self.producer.produce_to = hold_fixture
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending_result = executor.submit(self.runs.execute, pending.id)
            try:
                self.assertTrue(started.wait(10))
                producing_snapshot = business_snapshot(self.db)
                producing = self.detail(collection)
                self.assertTrue(any(row["run_id"] == pending.id and row["status"] == "producing"
                                    for row in producing["sources"]))
                self.assertEqual(business_snapshot(self.db), producing_snapshot)
                self.assertEqual(self.producer.calls, calls)
            finally:
                release.set()
            pending_result.result(timeout=20)
        self.producer.produce_to = original_produce
        after = business_snapshot(self.db)
        current = self.detail(collection)
        self.assertEqual(current["version"], collection["version"])
        self.assertNotEqual(current["snapshot_digest"], collection["snapshot_digest"])
        self.assertEqual([item["id"] for item in current["items"][:len(order)]], order)
        self.assertEqual([item["run_id"] for item in current["items"][len(order):]], [pending.id] * 2)
        self.assertFalse(current["items"][0]["included"])
        self.assertEqual(self.download(collection).status_code, 409)
        self.assertEqual(business_snapshot(self.db), after)

    def test_idempotency_input_conflict_cas_unknown_ids_and_collection_mismatch(self):
        collection = self.create()
        self.assertEqual(self.create()["id"], collection["id"])
        conflict = self.client.post(self.base, json=dict(name="changed", request_id="collection-request",
                                                       run_ids=[self.first.id]))
        self.assertEqual(conflict.status_code, 409, conflict.text)
        other = self.create(name="other", request_id="other", run_ids=[self.extra.id])
        for field in ("order_ids", "excluded_ids"):
            for identifier in ("unknown-image", other["items"][0]["id"]):
                response = self.client.patch(f"{self.base}/{collection['id']}", json={
                    "expected_version": collection["version"], field: [identifier]})
                self.assertEqual(response.status_code, 409, response.text)
        foreign_url = other["items"][0]["url"].replace(other["id"], collection["id"])
        self.assertIn(self.client.get(foreign_url).status_code, (404, 409))
        saved = self.patch(collection, name="renamed", run_ids=[self.first.id, self.extra.id])
        self.assertGreater(saved["version"], collection["version"])
        stale = self.client.patch(f"{self.base}/{collection['id']}", json={
            "expected_version": collection["version"], "name": "lost update"})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(self.detail(saved)["name"], "renamed")
        self.assertEqual(self.download(collection).status_code, 409)
        self.assertEqual(self.client.delete(f"{self.base}/{collection['id']}", params={
            "expected_version": collection["version"]}, headers={"Content-Type": "application/json"}).status_code, 409)

    def test_selection_zip_manifest_order_exact_bytes_and_delete_preserves_originals(self):
        collection = self.create(run_ids=[self.first.id, self.extra.id])
        before, files = business_snapshot(self.db), self.original_files()
        order = [item["id"] for item in reversed(collection["items"])]
        saved = self.patch(collection, order_ids=order, excluded_ids=[order[1]])
        self.assertEqual(self.detail(saved), saved)
        selected = [item for item in saved["items"] if item["included"]]
        raw = {item["id"]: self.client.get(item["url"]).content for item in selected}
        response = self.download(saved)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertEqual(response.headers["cache-control"], "no-store")
        with ZipFile(BytesIO(response.content)) as archive:
            expected_names = [f"{index:04d}.png" for index in range(1, len(selected) + 1)]
            self.assertEqual(archive.namelist(), expected_names + ["manifest.json"])
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["collection_id"], saved["id"])
            self.assertEqual(manifest["version"], saved["version"])
            self.assertEqual(manifest["snapshot_digest"], saved["snapshot_digest"])
            self.assertEqual(manifest["sources"], saved["sources"])
            self.assertEqual(manifest["selected_count"], len(selected))
            self.assertEqual(manifest["collected_count"], len(saved["items"]))
            self.assertEqual([row["id"] for row in manifest["items"]], [item["id"] for item in selected])
            for filename, item, row in zip(expected_names, selected, manifest["items"]):
                self.assertEqual(archive.read(filename), raw[item["id"]])
                for key in ("run_id", "revision_id", "revision_number", "order", "checksum"):
                    self.assertEqual(row[key], item[key])
                self.assertEqual(row["filename"], filename)
        bad_snapshot = self.client.get(f"{self.base}/{saved['id']}/download", params={
            "version": saved["version"], "snapshot": "0" * 64})
        self.assertEqual(bad_snapshot.status_code, 409)
        empty = self.patch(saved, excluded_ids=[item["id"] for item in saved["items"]])
        self.assertEqual(self.download(empty).status_code, 409)
        deleted = self.client.delete(f"{self.base}/{empty['id']}", params={"expected_version": empty["version"]},
                                     headers={"Content-Type": "application/json"})
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.client.get(f"{self.base}/{empty['id']}").status_code, 404)
        self.assertEqual(business_snapshot(self.db), before)
        self.assertEqual(self.original_files(), files)

    def test_similarity_sort_deterministic_persistent_and_never_changes_pixels(self):
        collection = self.create(run_ids=[self.first.id, self.extra.id])
        excluded = collection["items"][0]["id"]
        collection = self.patch(collection, excluded_ids=[excluded])
        before, files = business_snapshot(self.db), self.original_files()
        url = f"{self.base}/{collection['id']}/sort"
        response = self.client.post(url, json={"expected_version": collection["version"],
                                              "snapshot_digest": collection["snapshot_digest"]})
        self.assertEqual(response.status_code, 200, response.text)
        sorted_collection = response.json()
        order = [item["id"] for item in sorted_collection["items"]]
        self.assertCountEqual(order, [item["id"] for item in collection["items"]])
        self.assertEqual(order[-1], excluded)
        self.assertFalse(sorted_collection["items"][-1]["included"])
        self.assertEqual(self.detail(sorted_collection), sorted_collection)
        again = self.client.post(url, json={"expected_version": sorted_collection["version"],
            "snapshot_digest": sorted_collection["snapshot_digest"]})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual([item["id"] for item in again.json()["items"]], order)
        self.assertEqual(self.client.post(url, json={"expected_version": collection["version"],
            "snapshot_digest": collection["snapshot_digest"]}).status_code, 409)
        self.assertEqual(business_snapshot(self.db), before)
        self.assertEqual(self.original_files(), files)

    def test_source_catalogue_never_reads_files_and_query_count_does_not_grow_per_run(self):
        # These traps assert that the catalogue is metadata-only; they do not
        # replace artifact verification in the real collection/image/export tests.
        artifacts = self.app.state.media_collections.artifacts
        statements = []
        caller_thread = get_ident()

        def record_select(_connection, _cursor, statement, _parameters, _context, _executemany):
            if get_ident() == caller_thread and statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(self.db.engine, "before_cursor_execute", record_select)
        self.addCleanup(event.remove, self.db.engine, "before_cursor_execute", record_select)
        with patch.object(artifacts, "projection", side_effect=AssertionError("catalogue projected files")), \
             patch.object(artifacts, "locate", side_effect=AssertionError("catalogue located files")), \
             patch.object(artifacts, "image", side_effect=AssertionError("catalogue read image")), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("catalogue read bytes")), \
             patch.object(Path, "read_text", side_effect=AssertionError("catalogue read text")):
            response = self.client.get(self.base + "/sources")
            self.assertEqual(response.status_code, 200, response.text)
            initial_count = len(response.json()["items"])
            # Count the real service query on this thread to exclude unrelated
            # background worker polling from the HTTP application's lifespan.
            self.assertEqual(len(self.app.state.media_collections.sources("review-lab")), initial_count)
            initial_queries = len(statements)
        repo = ContentRepository(self.db)
        for number in range(3):
            identifier = f"catalogue-pending-{number}"
            repo.add_topic(topic_id=identifier, series_id="agent-notes", title=identifier, source=TopicSource.MANUAL)
            self.runs.create(identifier)
        statements.clear()
        sources = self.app.state.media_collections.sources("review-lab")
        self.assertEqual(len(sources), initial_count + 3)
        self.assertEqual(len(statements), initial_queries)
        self.assertLessEqual(initial_queries, 4)

    def test_one_hundred_images_selected_sort_and_zip_preserve_original_bytes(self):
        repo = ContentRepository(self.db)
        repo.add_topic(topic_id="hundred-images", series_id="agent-notes", title="100 local fixtures",
                       source=TopicSource.MANUAL)
        original_image_new = Image.new

        def small_local_image(mode, _size, color):
            return original_image_new(mode, (24, 32), tuple(channel % 256 for channel in color))

        # Restrict only local fixture creation to tiny deterministic images. The
        # producer, artifact validation, SQLite, HTTP, sort and ZIP remain real.
        with patch("tests.studio_review_fixtures.Image.new", side_effect=small_local_image):
            large = self.produce("hundred-images", 100)
        before, files = business_snapshot(self.db), self.original_files()
        collection = self.create(run_ids=[large.id])
        self.assertEqual(len(collection["items"]), 100)
        self.assertEqual({item["run_id"] for item in collection["items"]}, {large.id})
        excluded = [item["id"] for item in collection["items"][::5]]
        selected = self.patch(collection, excluded_ids=excluded)
        collection_service = self.app.state.media_collections
        with patch.object(collection_service, "_read_item", wraps=collection_service._read_item) as reads:
            response = self.client.post(f"{self.base}/{selected['id']}/sort", json={
                "expected_version": selected["version"], "snapshot_digest": selected["snapshot_digest"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(reads.call_args_list), 80)
            self.assertFalse({call.args[0]["id"] for call in reads.call_args_list} & set(excluded))
        sorted_collection = response.json()
        self.assertEqual(len(sorted_collection["items"]), 100)
        self.assertEqual([item["id"] for item in sorted_collection["items"][-20:]], excluded)
        included = [item for item in sorted_collection["items"] if item["included"]]
        self.assertEqual(len(included), 80)
        directory = Path(self.runs.get_active_revision(large.id).artifact_directory)
        originals = {item["id"]: (directory / f"images/{item['order']:02d}.png").read_bytes()
                     for item in included}
        exported = self.download(sorted_collection)
        self.assertEqual(exported.status_code, 200, exported.text)
        with ZipFile(BytesIO(exported.content)) as archive:
            filenames = [f"{number:04d}.png" for number in range(1, 81)]
            self.assertEqual(archive.namelist(), filenames + ["manifest.json"])
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["selected_count"], 80)
            self.assertEqual(manifest["collected_count"], 100)
            self.assertEqual(sum(source["image_count"] for source in manifest["sources"]), 100)
            self.assertEqual([row["id"] for row in manifest["items"]], [item["id"] for item in included])
            for filename, item in zip(filenames, included):
                self.assertEqual(archive.read(filename), originals[item["id"]])
        self.assertEqual(self.detail(sorted_collection), sorted_collection)
        self.assertEqual(business_snapshot(self.db), before)
        self.assertEqual(self.original_files(), files)

    def test_broken_source_is_explicit_keeps_healthy_source_and_rejects_old_snapshot(self):
        collection = self.create(run_ids=[self.first.id, self.extra.id])
        with self.db.session() as session:
            revision = session.get(ContentRevision, collection["items"][0]["revision_id"])
            image = Path(revision.artifact_directory) / "images/01.png"
        original = image.read_bytes()
        for damage in (b"not an image", original + b"changed", None):
            if damage is None:
                image.unlink()
            else:
                image.write_bytes(damage)
            current = self.detail(collection)
            self.assertTrue(any(row.get("error") for row in current["sources"]
                                if row["run_id"] == self.first.id))
            self.assertEqual({item["run_id"] for item in current["items"]}, {self.extra.id})
            self.assertEqual(self.download(collection).status_code, 409)
            self.assertIn(self.client.get(collection["items"][0]["url"]).status_code, (404, 409))
        image.write_bytes(original)
        self.assertEqual(self.detail(collection), collection)

    def test_cross_account_removed_and_moved_sources_cannot_read_images(self):
        repo = ContentRepository(self.db)
        repo.create_creator(creator_id="other-owner", display_name="Other")
        repo.create_series(series_id="other-series", creator_id="other-owner", name="Other",
                           description="", audience="", skill_name="knowledge-to-carousel")
        repo.add_topic(topic_id="other-topic", series_id="other-series", title="Other", source=TopicSource.MANUAL)
        foreign = self.produce("other-topic", 1)
        collection = self.create()
        original_url = collection["items"][0]["url"]
        foreign_base = "/api/creators/other-owner/collections"
        self.assertEqual(self.client.get(f"{foreign_base}/{collection['id']}").status_code, 404)
        self.assertEqual(self.client.get(original_url.replace("review-lab", "other-owner")).status_code, 404)
        foreign_resource = f"{foreign_base}/{collection['id']}"
        self.assertEqual(self.client.patch(foreign_resource, json={"expected_version": collection["version"],
            "name": "forbidden"}).status_code, 404)
        self.assertEqual(self.client.delete(foreign_resource, params={"expected_version": collection["version"]},
            headers={"Content-Type": "application/json"}).status_code, 404)
        self.assertEqual(self.client.post(foreign_resource + "/sort", json={
            "expected_version": collection["version"], "snapshot_digest": collection["snapshot_digest"]}).status_code, 404)
        self.assertEqual(self.client.get(foreign_resource + "/download", params={
            "version": collection["version"], "snapshot": collection["snapshot_digest"]}).status_code, 404)
        for payload in ({"run_ids": [foreign.id]}, {"topic_id": "other-topic", "run_ids": []}):
            response = self.client.post(self.base, json={"name": "forbidden", "request_id": "cross", **payload})
            self.assertEqual(response.status_code, 404, response.text)
        with self.db.session() as session:
            session.get(Series, "agent-notes").creator_id = "other-owner"
        self.assertIn(self.client.get(original_url).status_code, (404, 409))
        self.assertEqual(self.detail(collection)["items"], [])
        # Current ownership alone does not override the Run's frozen creator.
        response = self.client.post(foreign_base, json={"name": "moved", "request_id": "moved",
                                                       "run_ids": [self.first.id]})
        self.assertEqual(response.status_code, 409, response.text)
        with self.db.session() as session:
            session.get(Series, "agent-notes").creator_id = "review-lab"
            session.add(TopicRemoval(topic_id="review-1", series_id="agent-notes", creator_id="review-lab",
                                     request_id="removed", status="archived"))
        self.assertIn(self.client.get(original_url).status_code, (404, 409))
        self.assertEqual(self.detail(collection)["items"], [])
        self.assertEqual(self.client.post(self.base, json={"name": "removed", "request_id": "removed",
            "run_ids": [self.first.id]}).status_code, 404)

    def test_revision_frozen_identity_and_artifact_binding_are_verified(self):
        collection = self.create()
        revision_id = collection["items"][0]["revision_id"]
        with self.db.session() as session:
            revision = session.get(ContentRevision, revision_id)
            original = dict(revision.production_input_json)
            revision.production_input_json = {**original, "creator_id": "unrelated-owner"}
        self.assertEqual(self.detail(collection)["items"], [])
        self.assertIn(self.client.get(collection["items"][0]["url"]).status_code, (404, 409))
        with self.db.session() as session:
            revision = session.get(ContentRevision, revision_id)
            revision.production_input_json = original
            directory = revision.artifact_directory
            other_revision = self.runs.get_active_revision(self.extra.id)
            revision.artifact_directory = other_revision.artifact_directory
        self.assertEqual(self.detail(collection)["items"], [])
        with self.db.session() as session:
            session.get(ContentRevision, revision_id).artifact_directory = directory
        self.assertEqual(self.detail(collection), collection)


class MediaCollectionMigrationTests(unittest.TestCase):
    def test_additive_migration_preserves_old_rows_and_metadata_has_zero_drift(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = f"sqlite:///{(root / 'migration.db').as_posix()}"
            upgrade_database(url, "20261022_0017")
            database = Database(url)
            try:
                repo = ContentRepository(database)
                repo.create_creator(creator_id="old-owner", display_name="Original")
                repo.create_series(series_id="old-series", creator_id="old-owner", name="Original",
                                   description="unchanged", audience="unchanged", skill_name="knowledge-to-carousel")
                repo.add_topic(topic_id="old-topic", series_id="old-series", title="Original", source=TopicSource.MANUAL)
                # Seed the historical schema directly; current ORM contains
                # columns introduced by later migrations and cannot query v17.
                with database.engine.begin() as connection:
                    connection.execute(text("""INSERT INTO content_runs
                        (id,topic_id,idempotency_key,status,active_revision_number,input_snapshot_json,
                         retryable,version,created_at,updated_at)
                        VALUES ('old-run','old-topic','content:old-topic','queued',1,'{}',0,1,
                                '2026-10-01 00:00:00','2026-10-01 00:00:00')"""))
                before = business_snapshot(database)
            finally:
                database.close()
            upgrade_database(url)
            database = Database(url)
            try:
                after = business_snapshot(database)
                self.assertEqual(after.pop("production_tasks"), [])
                for row in after["content_runs"]:
                    self.assertIsNone(row.pop("task_id"))
                self.assertEqual(after, before)
                with database.engine.connect() as connection:
                    self.assertIn("media_collections", inspect(connection).get_table_names())
                    self.assertEqual(connection.execute(text("SELECT count(*) FROM media_collections")).scalar_one(), 0)
                    self.assertFalse(compare_metadata(MigrationContext.configure(connection), Base.metadata))
                    self.assertFalse(connection.execute(text("PRAGMA foreign_key_check")).all())
                    self.assertEqual(connection.execute(text("PRAGMA integrity_check")).scalar_one(), "ok")
            finally:
                database.close()
            command.downgrade(config(url), "20261022_0017")
            database = Database(url)
            try:
                self.assertEqual(business_snapshot(database), before)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
