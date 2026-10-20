"""Real isolated HTTP/SQLite read checks; no model calls or production execution."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from sqlalchemy import event

from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for, _write
from creatoros.integrations.topic_research import TopicResearchService
from creatoros.storage import ContentRepository, Database, upgrade_database
from creatoros.storage.models import Topic, TopicSource, ContentRun, ContentRunStatus
from creatoros.web import create_app
from creatoros.web.queries import StudioQueryService
from tests.agent_studio_support import serve
from tests.smoke_topic_research import seed_batch


class TopicLibraryOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        url = "sqlite:///" + (cls.root / "library.db").as_posix()
        upgrade_database(url)
        cls.db = Database(url)
        repo = ContentRepository(cls.db)
        repo.create_creator(creator_id="a", display_name="isolated")
        repo.create_series(series_id="s", creator_id="a", name="navigation",
                           description="", audience="", skill_name="knowledge-to-carousel")
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with cls.db.session() as session:
            for i in range(27):
                session.add(Topic(id=f"t{i:02}", series_id="s", title=f"选题 {i}",
                                  source=TopicSource.MANUAL, position=i + 1,
                                  created_at=start + timedelta(days=i),
                                  updated_at=start + timedelta(days=i)))
            session.flush()
            for run_id, topic_id, created, updated in [
                ("obsolete", "t00", 40, 100),
                ("current", "t00", 50, 52),
                ("new-output", "t01", 40, 60),
            ]:
                session.add(ContentRun(id=run_id, topic_id=topic_id, idempotency_key=run_id,
                    input_snapshot_json={}, status=ContentRunStatus.AWAITING_APPROVAL,
                    created_at=start + timedelta(days=created), updated_at=start + timedelta(days=updated)))
        cls.service = TopicResearchService(cls.db, ProducerSkillCatalog(skills_root_for(cls.db)))
        batch = seed_batch(cls.service, "s")
        batch["created_at"] = (start + timedelta(days=55)).isoformat()
        _write(cls.service._path(batch["id"]), batch)
        cls.pending_ids = sorted(cls.service.topic_id(batch["id"], candidate["id"])
                                 for candidate in batch["candidates"])
        cls.server = serve(create_app(database=cls.db, topic_research_service=cls.service))
        cls.client = httpx.Client(base_url=cls.server.__enter__(), timeout=20, trust_env=False)
        cls.before = cls.database_rows()
        cls.batch_before = cls.service._path(batch["id"]).read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.server.__exit__(None, None, None)
        cls.db.close()
        cls.tmp.cleanup()

    @classmethod
    def database_rows(cls):
        with cls.db.engine.connect() as connection:
            tables = connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").scalars()
            return {name: sorted(repr(tuple(row)) for row in connection.exec_driver_sql(f'SELECT * FROM "{name}"'))
                    for name in tables}

    def get(self, **params):
        response = self.client.get("/api/series/s/topic-library", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_default_order_remains_queue_and_invalid_order_rejected(self):
        implicit = self.get(limit=100)
        self.assertEqual(implicit, self.get(order="queue", limit=100))
        self.assertEqual([row["id"] for row in implicit["items"][:27]], [f"t{i:02}" for i in range(27)])
        self.assertEqual(self.client.get("/api/series/s/topic-library?order=wrong").status_code, 422)
        self.assertEqual(self.client.get("/api/series/missing/topic-library?order=recent").status_code, 404)

    def test_recent_global_sort_before_pagination_and_latest_run_not_old_run(self):
        first, second = self.get(order="recent"), self.get(order="recent", offset=20)
        actual = [row["id"] for row in first["items"] + second["items"]]
        self.assertEqual(actual, ["t01", *self.pending_ids, "t00", *[f"t{i:02}" for i in range(26, 1, -1)]])
        self.assertEqual(first["page"]["total"], 29)
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(len(second["items"]), 9)
        self.assertEqual(first, self.get(order="recent"))
        self.assertEqual(first["items"][0]["existing_run_id"], "new-output")
        self.assertEqual(first["items"][3]["existing_run_id"], "current")

    def test_filters_and_nonstandard_page_size(self):
        queued = self.get(order="recent", state="queued", limit=6)
        self.assertEqual([row["id"] for row in queued["items"]], ["t01", "t00", "t26", "t25", "t24", "t23"])
        self.assertEqual(queued["page"]["total"], 27)
        pending = self.get(order="recent", state="pending")
        self.assertEqual([row["id"] for row in pending["items"]], self.pending_ids)
        self.assertEqual(pending["page"]["total"], 2)
        self.assertEqual(self.get(order="recent", offset=100)["items"], [])

    def test_read_sort_does_not_mutate_any_database_record_or_candidate_file(self):
        for order in ("queue", "recent"):
            for state in ("all", "pending", "queued"):
                self.get(order=order, state=state)
        self.assertEqual(self.database_rows(), self.before)
        self.assertEqual(self.service._path("a" * 32).read_bytes(), self.batch_before)

    def test_activity_projection_is_batched_not_one_query_per_topic(self):
        statements = []
        def observe(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)
        event.listen(self.db.engine, "before_cursor_execute", observe)
        try:
            queries = StudioQueryService(self.db)
            values = queries.topic_activity_times("s")
        finally:
            event.remove(self.db.engine, "before_cursor_execute", observe)
        self.assertEqual(len(values), 27)
        self.assertEqual(len(statements), 2)


if __name__ == "__main__":
    unittest.main()
