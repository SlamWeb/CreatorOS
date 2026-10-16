"""Irreversible posting is fault-injected; these tests never contact XHS."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from creatoros.publication import PublicationService
from creatoros.runs import ContentRunError
from creatoros.storage import Base, ContentRun, ContentRevision, PublicationRequest, PublicationMetric, ManualPublication
from creatoros.web.artifacts import StudioArtifacts
from tests.studio_review_fixtures import make_fixture


class ControlledPlatform:
    def __init__(self):
        self.account = "stable-xhs-owner"
        self.calls = 0
        self.timeout = False
        self.notes = []
        self.release = asyncio.Event()
        self.release.set()

    async def identity(self):
        return {"id": self.account, "name": "隔离账号"}

    async def publish(self, payload, publication_id, local_images):
        self.calls += 1
        await self.release.wait()
        self.notes = [{"id": f"isolated-note-{self.calls}", "author_id": self.account, "title": payload["title"],
            "body": payload["content"], "images": ["https://example.com/image"] * len(local_images),
            "published_at": datetime.now(timezone.utc).isoformat(),
            "url": f"https://www.xiaohongshu.com/explore/isolated-note-{self.calls}", "metrics": {"likes": 3}}]
        if self.timeout:
            raise TimeoutError("simulated ambiguous network outcome")
        return {"transport_completed": True}

    async def find_candidates(self, payload, started_at):
        return self.notes

    async def inspect(self, candidate):
        return candidate if isinstance(candidate, dict) else self.notes[0]


async def expect_error(awaitable, code):
    try:
        await awaitable
    except ContentRunError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError(f"Expected {code}")


async def main():
    with tempfile.TemporaryDirectory(prefix="creatoros-publish-") as directory:
        root = Path(directory)
        db, runs, _, _ = make_fixture(root)
        platform = ControlledPlatform()
        service = PublicationService(db, runs, StudioArtifacts(db, runs.output_root), platform)
        with db.engine.connect() as connection:
            drift = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert drift == [], drift
        run = runs.create("review-1")
        runs.execute(run.id)
        draft = await service.prepare(run.id, title="四个常用词")
        assert draft["status"] == "draft" and not draft["can_confirm"]
        await expect_error(service.confirm(draft["id"], draft["digest"]), "approval_required")
        with db.session() as session:
            current = session.get(ContentRun, run.id)
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run.id))
        runs.approve(run.id, revision_id=revision.id, artifact_digest=revision.artifact_digest, expected_version=current.version)
        changed = await service.prepare(run.id, title="四个英语词")
        await expect_error(service.confirm(draft["id"], draft["digest"]), "preview_changed")
        platform.account = "wrong-account"
        await expect_error(service.confirm(changed["id"], changed["digest"]), "account_mismatch")
        platform.account = "stable-xhs-owner"
        # Even an approved run cannot publish bytes changed after the preview.
        image_path = Path(revision.artifact_directory) / "images/01.png"
        original_bytes = image_path.read_bytes()
        try:
            image_path.write_bytes(original_bytes + b"changed-after-preview")
            await expect_error(service.confirm(changed["id"], changed["digest"]), "artifact_changed")
        finally:
            image_path.write_bytes(original_bytes)
        # Two sessions/clicks must share a single dispatch, even while it is running.
        platform.release.clear()
        first, second = await asyncio.gather(service.confirm(changed["id"], changed["digest"]), service.confirm(changed["id"], changed["digest"]))
        assert first["status"] == second["status"] == "sending"
        await asyncio.sleep(0)
        assert platform.calls == 1
        platform.release.set()
        await asyncio.gather(*list(service.tasks))
        assert service.get(changed["id"])["status"] == "published"
        await service.confirm(changed["id"], changed["digest"])
        assert platform.calls == 1
        metric = await service.refresh_metrics(changed["id"])
        assert metric["metrics"][0]["likes"] == 3 and metric["metrics"][0]["views"] is None
        with db.session() as session:
            assert len(list(session.scalars(select(ManualPublication)))) == 1
            assert len(list(session.scalars(select(PublicationMetric)))) == 1
        # New run has a separate draft, but an ambiguous call is never retried.
        run2 = runs.create("review-2")
        runs.execute(run2.id)
        await expect_error(service.prepare(run2.id, title="四个英语词"), "duplicate_content")
        platform.account = "wrong-account"
        await expect_error(service.prepare(run2.id, title="另一组词"), "account_mismatch")
        platform.account = "stable-xhs-owner"
        other = await service.prepare(run2.id, title="另一组词")
        with db.session() as session:
            current = session.get(ContentRun, run2.id)
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run2.id))
        runs.approve(run2.id, revision_id=revision.id, artifact_digest=revision.artifact_digest, expected_version=current.version)
        platform.timeout = True
        await service.confirm(other["id"], other["digest"])
        await asyncio.gather(*list(service.tasks))
        assert service.get(other["id"])["status"] == "unknown"
        await service.confirm(other["id"], other["digest"])
        assert platform.calls == 2
        # Tool success alone, wrong author, same title, missing timestamp: no receipt.
        original = dict(platform.notes[0])
        platform.notes[0]["body"] = "same title is not evidence"
        assert (await service.reconcile(other["id"]))["status"] == "unknown"
        platform.notes[0] = {**original, "author_id": "someone-else"}
        assert (await service.reconcile(other["id"]))["status"] == "unknown"
        platform.notes[0] = {**original, "published_at": None}
        assert (await service.reconcile(other["id"]))["status"] == "unknown"
        # Explicit user link + exact author/copy/image count can resolve uncertainty.
        assert (await service.reconcile(other["id"], original["url"]))["status"] == "published"
        with db.session() as session:
            row = session.get(PublicationRequest, other["id"])
            row.status = "sending"
        service.start()
        assert service.get(other["id"])["status"] == "unknown"
        assert platform.calls == 2
        await service.shutdown()
        db.close()
    print("publication_requests_smoke=passed migration_no_drift=passed confirmation=passed dedupe=passed uncertain_no_resend=passed")


if __name__ == "__main__":
    asyncio.run(main())
