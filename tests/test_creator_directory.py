"""Lightweight account-directory contract over an isolated SQLite and HTTP server."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from threading import Event
import unittest

import httpx
from sqlalchemy import event

from creatoros.storage import Creator, CreatorPlatform, Database, upgrade_database
from creatoros.web.app import create_app
from tests.agent_studio_support import serve


class CreatorDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="creator-directory-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        url = f"sqlite:///{(self.root / 'studio.db').as_posix()}"
        upgrade_database(url)
        self.database = Database(url)
        self.addCleanup(self.database.close)
        self.app = create_app(database=self.database, chat_root=self.root / "chat", defer_background_start=True)
        self.server = serve(self.app)
        self.url = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.client = httpx.Client(base_url=self.url, timeout=15, trust_env=False)
        self.addCleanup(self.client.close)

    def test_empty_directory_and_legacy_page_are_both_available(self):
        response = self.client.get("/api/creator-directory")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"items": []})
        old = self.client.get("/api/creators?offset=0&limit=100")
        self.assertEqual(old.status_code, 200, old.text)
        self.assertEqual(old.json()["items"], [])
        self.assertEqual(old.json()["page"]["total"], 0)

    def seed_accounts(self, count):
        with self.database.session() as session:
            session.add_all([Creator(id=f"creator-{index:04d}", display_name=f"账号 {index:04d}", platform=CreatorPlatform.XIAOHONGSHU)
                             for index in range(count)])

    def test_directory_returns_more_than_a_thousand_accounts_and_scoped_agent_cannot_enumerate(self):
        self.seed_accounts(1001)
        response = self.client.get("/api/creator-directory")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        items = response.json()["items"]
        self.assertEqual(len(items), 1001)
        self.assertEqual({item["id"] for item in items}, {f"creator-{index:04d}" for index in range(1001)})
        self.assertTrue(all(set(item) == {"id", "display_name"} for item in items))

        old = self.client.get("/api/creators?offset=100&limit=100")
        self.assertEqual(old.status_code, 200, old.text)
        self.assertEqual(old.json()["page"]["total"], 1001)
        self.assertEqual(len(old.json()["items"]), 100)
        self.assertIn("series", old.json()["items"][0])

        sid = self.app.state.chat.create("creator-0000")["id"]
        scoped = {"x-creatoros-agent-session": sid}
        self.assertEqual(self.client.get("/api/creator-directory", headers=scoped).status_code, 403)
        self.assertEqual(self.client.get("/api/creator-directory", headers={
            "x-creatoros-agent-session": "missing-session"}).status_code, 403)
        old_scoped = self.client.get("/api/creators?offset=0&limit=100", headers=scoped)
        self.assertEqual(old_scoped.status_code, 200)
        self.assertEqual([item["id"] for item in old_scoped.json()["items"]], ["creator-0000"])

    def test_one_directory_response_is_a_consistent_sqlite_snapshot_during_concurrent_delete_and_add(self):
        self.seed_accounts(103)
        with self.database.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar().lower(), "wal")
        before = self.client.get("/api/creator-directory").json()["items"]
        victim = before[0]["id"]
        ready, release = Event(), Event()

        def hold_directory_read(connection, cursor, statement, parameters, context, executemany):
            sql = " ".join(statement.lower().split())
            # The delete endpoint also selects Creator, including these two
            # columns. Only pause the directory's two-column projection.
            if sql.startswith("select creators.id, creators.display_name from creators"):
                ready.set()
                if not release.wait(15):
                    raise TimeoutError("directory snapshot gate was not released")

        event.listen(self.database.engine, "after_cursor_execute", hold_directory_read)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.client.get, "/api/creator-directory")
                try:
                    self.assertTrue(ready.wait(10), "directory SELECT did not reach the gate")
                    removed = self.client.post(f"/api/creators/{victim}/delete", json={})
                    self.assertEqual(removed.status_code, 200, removed.text)
                    created = self.client.post("/api/creators", json={"display_name": "并发新建账号"})
                    self.assertEqual(created.status_code, 201, created.text)
                    new_id = created.json()["id"]
                finally:
                    release.set()
                snapshot = pending.result(timeout=15)
                self.assertEqual(snapshot.status_code, 200, snapshot.text)
                self.assertEqual(snapshot.json()["items"], before)
                self.assertNotIn(new_id, [item["id"] for item in snapshot.json()["items"]])
        finally:
            release.set()
            event.remove(self.database.engine, "after_cursor_execute", hold_directory_read)

        after = self.client.get("/api/creator-directory").json()["items"]
        self.assertEqual(len(after), len(before))
        self.assertNotIn(victim, [item["id"] for item in after])
        self.assertIn(new_id, [item["id"] for item in after])


if __name__ == "__main__":
    unittest.main()
