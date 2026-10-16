"""A durable publication intent. Only a frontend confirmation may dispatch it.

An ambiguous external outcome is never retried: the platform has no idempotency key.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import wraps
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from creatoros.runs import ContentRunError
from creatoros.runs.artifacts import validate_artifact
from creatoros.storage import (ContentRevision, ContentRun, ContentRunStatus,
    Creator, ManualPublication, PublicationAccount, PublicationMetric,
    PublicationRequest, PublicationFeedbackState, Series, Topic, TopicStatus)


def now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fail(message, code="publication_conflict", status=409):
    raise ContentRunError(message, code=code, status_code=status)


def event(row, kind, message, evidence=None):
    entry = {"at": now().isoformat(), "type": kind, "message": message}
    if evidence is not None:
        entry["evidence"] = evidence
    row.events_json = [*(row.events_json or []), entry]


class PublicationBusy(Exception):
    """A background read yields to an active or queued foreground operation."""


def mcp_operation(function):
    @wraps(function)
    async def guarded(self, *args, **kwargs):
        background = kwargs.pop("background", False)
        try:
            async with self.mcp_access(background=background):
                return await function(self, *args, **kwargs)
        except asyncio.CancelledError:
            if function.__name__ == "_dispatch":
                self._unknown(args[0], "执行中断，平台结果未知；不会自动重新发布。")
            raise
    return guarded


class PublicationService:
    def __init__(self, database, runs, artifacts, client):
        self.database, self.runs, self.artifacts, self.client = database, runs, artifacts, client
        self.tasks = set()
        self._mcp_lock = asyncio.Lock()
        self._mcp_owner = None
        self._foreground_waiters = 0
        self.snapshot_root = Path(runs.output_root) / ".publication-snapshots"

    @asynccontextmanager
    async def mcp_access(self, *, background=False):
        # Reentrant only for the very same task (dispatch -> reconcile).
        task = asyncio.current_task()
        if self._mcp_owner is task:
            yield
            return
        if background and (self._mcp_lock.locked() or self._foreground_waiters or self.tasks):
            raise PublicationBusy()
        if not background:
            self._foreground_waiters += 1
        try:
            await self._mcp_lock.acquire()
        finally:
            if not background:
                self._foreground_waiters -= 1
        self._mcp_owner = task
        try:
            yield
        finally:
            self._mcp_owner = None
            self._mcp_lock.release()

    def start(self):
        with self.database.session() as session:
            for row in session.scalars(select(PublicationRequest).where(PublicationRequest.status.in_(["sending", "verifying"]))):
                row.status = "unknown"
                row.last_error = "服务曾中断；请核对平台结果，不会自动重复发布。"
                event(row, "recovered", row.last_error)

    async def shutdown(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _source(self, run_id):
        with self.database.session() as session:
            run = session.get(ContentRun, run_id)
            if run is None:
                fail("内容不存在。", "not_found", 404)
            topic = session.get(Topic, run.topic_id)
            series = session.get(Series, topic.series_id) if topic else None
            creator = session.get(Creator, series.creator_id) if series else None
            if not creator or not creator.is_active or not series.is_active:
                fail("请先将内容归属到有效账号与栏目。")
            revision = session.scalar(select(ContentRevision).where(ContentRevision.content_run_id == run_id,
                ContentRevision.revision_number == run.active_revision_number))
            if not revision or not revision.artifact_digest:
                fail("当前内容还没有可发布的完整产物。")
        root, recorded_digest, data, _ = self.artifacts.locate(run_id, revision.id)
        if data.creator_id != creator.id or data.series_id != series.id:
            fail("内容生产时的账号归属已变化，请人工核对。", "account_mismatch")
        checked = validate_artifact(root, composition=data.composition,
            production_protocol=getattr(data, "production_protocol", "legacy"))
        if checked.artifact_digest != recorded_digest:
            fail("产物已变化，请重新验收。", "artifact_changed")
        pack = self.artifacts.pack(root, data)
        paths = [(root / card.image_path).resolve() for card in pack.cards]
        if any(not path.is_relative_to(root) for path in paths):
            fail("图片路径越界。")
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        return run, revision, creator, pack, paths, hashes

    @staticmethod
    def _approved(run, revision):
        return (run.status == ContentRunStatus.APPROVED and run.approved_revision_id == revision.id
                and run.approved_artifact_digest == revision.artifact_digest)

    @staticmethod
    def _validate_copy(payload):
        title = payload["title"].strip()
        # Upstream counts ASCII as half of a Chinese character (round up).
        units = sum(1 if ord(char) < 128 else 2 for char in title)
        if not title or units > 40:
            fail("标题请控制在 20 个中文字以内。", "invalid_title", 422)
        if not payload["content"].strip():
            fail("请填写发布正文。", "invalid_content", 422)
        if payload["visibility"] not in {"公开可见", "仅自己可见", "仅互关好友可见"}:
            fail("不支持此可见范围。", "invalid_visibility", 422)
        if not 1 <= payload["image_count"] <= 18:
            fail("小红书图文发布需要 1 至 18 张图片。", "invalid_images", 422)

    def _binding_check(self, session, creator_id, account_id):
        binding = session.get(PublicationAccount, creator_id)
        other = session.scalar(select(PublicationAccount).where(PublicationAccount.account_id == account_id))
        if (binding and binding.account_id != account_id) or (other and other.creator_id != creator_id):
            fail("当前登录的小红书账号与此 CreatorOS 账号不匹配。", "account_mismatch")

    @mcp_operation
    async def prepare(self, run_id, title=None, body=None, tags=None, visibility="公开可见"):
        identity = await self.client.identity()
        if not identity.get("id"):
            fail("无法确认小红书账号身份，请先登录。", "identity_unavailable")
        run, revision, creator, pack, paths, hashes = self._source(run_id)
        payload = {"account_id": identity["id"], "title": title if title is not None else pack.publish_copy.title,
            "content": body if body is not None else pack.publish_copy.body,
            "tags": list(dict.fromkeys(tag.strip().lstrip("#") for tag in (tags if tags is not None else pack.publish_copy.hashtags) if tag.strip().lstrip("#"))),
            "visibility": visibility, "image_hashes": hashes, "image_count": len(paths)}
        content_hash = digest(payload)
        frozen_hash = digest({"content": payload, "revision": revision.id, "artifact": revision.artifact_digest})
        try:
            with self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                self._binding_check(session, creator.id, identity["id"])
                row = session.scalar(select(PublicationRequest).where(PublicationRequest.content_run_id == run_id))
                if row and row.status != "draft":
                    return self._view(row, session)
                other = session.scalar(select(PublicationRequest).where(PublicationRequest.account_id == identity["id"],
                    PublicationRequest.content_digest == content_hash, PublicationRequest.content_run_id != run_id))
                if other:
                    fail("相同内容已有发布单，请查看原发布单，避免重复发布。", "duplicate_content")
                if session.scalar(select(ManualPublication).where(ManualPublication.content_run_id == run_id)):
                    fail("该内容已有发布记录，不能重复发布。", "already_published")
                if not row:
                    row = PublicationRequest(id=str(uuid4()), content_run_id=run_id, creator_id=creator.id, status="draft")
                    session.add(row)
                row.revision_id, row.artifact_digest = revision.id, revision.artifact_digest
                row.account_id, row.account_name = identity["id"], identity.get("name") or identity["id"]
                row.digest, row.content_digest, row.payload_json = frozen_hash, content_hash, payload
                row.confirmed_at, row.last_error = None, None
                event(row, "prepared", "发布预览已更新，等待本人确认。", {"digest": frozen_hash})
                session.flush()
                request_id = row.id
        except IntegrityError:
            fail("发布单已由另一请求更新，请刷新。", "publication_conflict")
        return self.get(request_id)

    def _view(self, row, session):
        run = session.get(ContentRun, row.content_run_id)
        revision = session.get(ContentRevision, row.revision_id)
        approved = bool(run and revision and run.active_revision_number == revision.revision_number and self._approved(run, revision))
        blockers = []
        if not approved:
            blockers.append("请先验收并批准当前内容。")
        try:
            self._validate_copy(row.payload_json)
        except ContentRunError as exc:
            blockers.append(str(exc))
        payload = dict(row.payload_json)
        payload["body"] = payload.pop("content")
        payload["images"] = [{"order": index + 1, "sha256": image_hash,
            "url": f"/api/runs/{row.content_run_id}/revisions/{row.revision_id}/cards/{index + 1}?digest={row.artifact_digest}&checksum={image_hash}"}
            for index, image_hash in enumerate(payload["image_hashes"])]
        receipt = session.scalar(select(ManualPublication).where(ManualPublication.content_run_id == row.content_run_id))
        metrics = [] if not receipt else [{"measured_at": utc(item.measured_at).isoformat(),
            **{key: getattr(item, key) for key in ("views", "likes", "favorites", "comments", "shares")}}
            for item in session.scalars(select(PublicationMetric).where(PublicationMetric.publication_id == receipt.id).order_by(PublicationMetric.measured_at))]
        return {"id": row.id, "run_id": row.content_run_id, "creator_id": row.creator_id,
            "revision_id": row.revision_id, "status": row.status, "digest": row.digest,
            "account": {"id": row.account_id, "name": row.account_name}, "payload": payload,
            "can_confirm": row.status == "draft" and not blockers, "blockers": blockers,
            "post_url": row.post_url, "last_error": row.last_error, "metrics": metrics,
            "events": row.events_json or []}

    def get(self, request_id):
        with self.database.session() as session:
            row = session.get(PublicationRequest, request_id)
            if not row:
                fail("发布单不存在。", "not_found", 404)
            return self._view(row, session)

    def current(self, run_id):
        with self.database.session() as session:
            row = session.scalar(select(PublicationRequest).where(PublicationRequest.content_run_id == run_id))
            return self._view(row, session) if row else None

    @mcp_operation
    async def confirm(self, request_id, expected_digest):
        current = self.get(request_id)
        if current["digest"] != expected_digest:
            fail("预览已变化，请重新确认。", "preview_changed")
        if current["status"] != "draft":
            return current
        identity = await self.client.identity()
        if identity.get("id") != current["account"]["id"]:
            fail("登录账号已变化，请重新准备发布单。", "account_mismatch")
        run, revision, creator, _, paths, hashes = self._source(current["run_id"])
        if creator.id != current["creator_id"]:
            fail("栏目归属账号已变化，请重新准备发布单。", "account_mismatch")
        if not self._approved(run, revision) or revision.id != current["revision_id"]:
            fail("请先验收并批准当前内容。", "approval_required")
        with self.database.session() as session:
            expected_artifact = session.get(PublicationRequest, request_id).artifact_digest
        if revision.artifact_digest != expected_artifact:
            fail("验收产物已变化，请重新准备发布单。", "artifact_changed")
        if hashes != current["payload"]["image_hashes"]:
            fail("图片已变化，请重新准备发布单。", "artifact_changed")
        # Copy before claiming; no platform side effect yet. Verify the copied bytes.
        directory = self.snapshot_root / request_id / expected_digest
        output_root = Path(self.runs.output_root).resolve()
        for parent in [directory, *directory.parents]:
            if parent.exists() and (parent.is_symlink() or bool(getattr(os.lstat(parent), "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))):
                fail("发布快照路径不是受信任的普通目录。", "unsafe_snapshot")
            if parent == output_root:
                break
        if not directory.resolve().is_relative_to(output_root):
            fail("发布快照路径越界。", "unsafe_snapshot")
        directory.mkdir(parents=True, exist_ok=True)
        frozen_images = []
        for index, path in enumerate(paths):
            target = directory / f"{index + 1:02d}{path.suffix}"
            if target.is_symlink():
                fail("发布快照文件不可为链接。", "unsafe_snapshot")
            shutil.copyfile(path, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != hashes[index]:
                fail("图片在确认时发生变化，请重新准备。", "artifact_changed")
            frozen_images.append(target)
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(PublicationRequest, request_id)
            if row.digest != expected_digest:
                fail("预览已变化，请重新确认。", "preview_changed")
            if row.status != "draft":
                return self._view(row, session)
            self._validate_copy(row.payload_json)
            self._binding_check(session, row.creator_id, row.account_id)
            fresh_run = session.get(ContentRun, row.content_run_id)
            fresh_rev = session.get(ContentRevision, row.revision_id)
            fresh_topic = session.get(Topic, fresh_run.topic_id)
            fresh_series = session.get(Series, fresh_topic.series_id) if fresh_topic else None
            fresh_creator = session.get(Creator, row.creator_id)
            if not fresh_series or fresh_series.creator_id != row.creator_id or not fresh_series.is_active or not fresh_creator or not fresh_creator.is_active:
                fail("栏目归属已变化，请重新准备。", "account_mismatch")
            if not self._approved(fresh_run, fresh_rev) or fresh_run.active_revision_number != fresh_rev.revision_number:
                fail("审批已变化，请重新验收。", "approval_required")
            if fresh_rev.artifact_digest != row.artifact_digest:
                fail("验收产物已变化，请重新准备。", "artifact_changed")
            if session.scalar(select(ManualPublication).where(ManualPublication.content_run_id == row.content_run_id)):
                fail("该内容已有发布记录，不能重复发布。", "already_published")
            if not session.get(PublicationAccount, row.creator_id):
                session.add(PublicationAccount(creator_id=row.creator_id, account_id=row.account_id,
                    account_name=row.account_name, confirmed_at=now()))
            row.status, row.confirmed_at, row.started_at = "sending", now(), now()
            event(row, "confirmed", "本人确认此账号、内容和可见范围；发布请求只发送一次。", {"digest": row.digest, "account_id": row.account_id})
        task = asyncio.create_task(self._dispatch(request_id, frozen_images))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return self.get(request_id)

    def _unknown(self, request_id, message):
        with self.database.session() as session:
            row = session.get(PublicationRequest, request_id)
            if row and row.status != "published":
                row.status, row.last_error = "unknown", message
                event(row, "needs_verification", message)

    @mcp_operation
    async def _dispatch(self, request_id, paths):
        try:
            with self.database.session() as session:
                row = session.get(PublicationRequest, request_id)
                payload = dict(row.payload_json)
            # Recheck identity immediately before the irreversible operation.
            if (await self.client.identity()).get("id") != payload["account_id"]:
                self._unknown(request_id, "执行前登录账号发生变化；未调用发布，请人工核对。")
                return
            receipt = await self.client.publish(payload, request_id, paths)
            with self.database.session() as session:
                row = session.get(PublicationRequest, request_id)
                if row.status != "published":
                    row.status = "verifying"
                    event(row, "submitted", "平台工具已返回，正在核对实际笔记；工具返回不等于发布成功。",
                        {"transport_completed": bool(receipt.get("transport_completed")), "verification_required": True})
            await self.reconcile(request_id)
        except asyncio.CancelledError:
            self._unknown(request_id, "执行中断，平台结果未知；不会自动重新发布。")
            raise
        except Exception:
            # Do not expose transport headers, cookie values or arbitrary upstream error text.
            self._unknown(request_id, "发布或核对未完成，平台结果未知；请核对结果，不要重复发布。")

    @staticmethod
    def _body_match(note, payload):
        """Compare prose separately from appended platform topic labels.

        XHS may canonicalize a selected topic (e.g. 英语学习 -> 英语学习打卡).
        Only a complete trailing topic block is metadata, never inline prose.
        """
        compact = lambda value: "".join(str(value or "").split())
        body = str(note.get("body") or "")
        expected = compact(payload["content"])
        requested = payload["tags"]
        actual = []
        if compact(body) != expected:
            marker = r"#([^#\r\n]+?)\[话题\]#"
            trailer = re.search(r"(?:\s*" + marker + r")+\s*\Z", body)
            if trailer and requested:
                actual = re.findall(marker, trailer[0])
                if len(actual) != len(requested):
                    return None
                body = body[:trailer.start()]
            elif requested:
                # Older responses use plain hashtags: accept exact labels only.
                suffix = r"\s+" + r"\s+".join(re.escape("#" + tag) for tag in requested) + r"\s*\Z"
                trailer = re.search(suffix, body)
                if not trailer:
                    return None
                actual, body = list(requested), body[:trailer.start()]
            if compact(body) != expected:
                return None
        return {"requested_topics": list(requested), "platform_topics": actual,
                "topic_labels_changed": bool(actual and actual != requested)}

    @staticmethod
    def _matches(note, payload, started_at, manual):
        compact = lambda value: "".join(str(value or "").split())
        if (note.get("author_id") != payload["account_id"] or
            compact(note.get("title")) != compact(payload["title"]) or
            PublicationService._body_match(note, payload) is None or
            len(note.get("images") or []) != payload["image_count"] or not note.get("id") or not note.get("url")):
            return False
        if manual:
            return True
        try:
            published = note.get("published_at")
            published = datetime.fromisoformat(published.replace("Z", "+00:00")) if isinstance(published, str) else published
            return utc(started_at) - timedelta(minutes=2) <= utc(published) <= min(now() + timedelta(minutes=2), utc(started_at) + timedelta(minutes=15))
        except (AttributeError, TypeError, ValueError):
            return False

    @mcp_operation
    async def reconcile(self, request_id, post_url=None):
        with self.database.session() as session:
            row = session.get(PublicationRequest, request_id)
            if not row:
                fail("发布单不存在。", "not_found", 404)
            if row.status == "published":
                return self._view(row, session)
            if row.status == "draft":
                fail("该发布单尚未确认。")
            payload, started_at = dict(row.payload_json), row.started_at
        if (await self.client.identity()).get("id") != payload["account_id"]:
            fail("当前登录账号与发布单不匹配。", "account_mismatch")
        candidates = [post_url] if post_url else await self.client.find_candidates(payload, started_at)
        matches = []
        for candidate in candidates:
            note = await self.client.inspect(candidate)
            if self._matches(note, payload, started_at, bool(post_url)):
                matches.append(note)
        unique = {note["id"]: note for note in matches}
        if len(unique) != 1:
            self._unknown(request_id, "尚未找到唯一匹配的笔记；可粘贴本人已发布的笔记链接核对。")
            return self.get(request_id)
        note = next(iter(unique.values()))
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(PublicationRequest, request_id)
            receipt = session.scalar(select(ManualPublication).where(ManualPublication.content_run_id == row.content_run_id))
            other_receipt = session.scalar(select(ManualPublication).where(ManualPublication.post_url == note["url"],
                ManualPublication.content_run_id != row.content_run_id))
            if other_receipt:
                fail("这条笔记已经关联到另一份内容，请人工核对。", "duplicate_remote_note")
            if receipt and receipt.post_url != note["url"]:
                fail("已有另一条发布记录，请人工核对。")
            if not receipt:
                session.add(ManualPublication(id=str(uuid4()), content_run_id=row.content_run_id,
                    revision_id=row.revision_id, artifact_digest=row.artifact_digest, platform="xiaohongshu",
                    post_url=note["url"], published_at=utc(started_at), recorded_at=now()))
            row.status, row.post_url, row.last_error = "published", note["url"], None
            event(row, "verified", "已按账号、完整文案、图片数量核对笔记" + ("（本人提供链接）。" if post_url else "及发布时间。"),
                {"note_id": note["id"], "author_id": note["author_id"], "image_count": len(note["images"]),
                 "published_at": note.get("published_at"), "text_match": True,
                 **self._body_match(note, payload), "method": "user_link" if post_url else "recent_notes"})
            run = session.get(ContentRun, row.content_run_id)
            topic = session.get(Topic, run.topic_id)
            if topic:
                topic.status = TopicStatus.PUBLISHED
        return self.get(request_id)

    @mcp_operation
    async def refresh_metrics(self, request_id, *, min_interval_seconds=0):
        view = self.get(request_id)
        if view["status"] != "published":
            fail("请先核对发布结果。")
        # Recheck after acquiring the shared browser gate: a manual refresh may
        # have completed while this scheduled check was being prepared.
        if view["metrics"] and min_interval_seconds:
            latest = datetime.fromisoformat(view["metrics"][-1]["measured_at"])
            if now() < latest + timedelta(seconds=min_interval_seconds):
                return view
        if (await self.client.identity()).get("id") != view["account"]["id"]:
            fail("当前登录账号不匹配。", "account_mismatch")
        note = await self.client.inspect(view["post_url"])
        if note.get("author_id") != view["account"]["id"]:
            fail("笔记作者不匹配。", "account_mismatch")
        metrics = {key: value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
            for key in ("views", "likes", "favorites", "comments", "shares")
            for value in [(note.get("metrics") or {}).get(key)]}
        with self.database.session() as session:
            receipt = session.scalar(select(ManualPublication).where(ManualPublication.content_run_id == view["run_id"]))
            measured_at = now()
            session.add(PublicationMetric(id=str(uuid4()), publication_id=receipt.id, request_id=str(uuid4()),
                measured_at=measured_at, **metrics))
            state = session.get(PublicationFeedbackState, request_id)
            if state:
                state.failures, state.last_error = 0, None
                state.next_attempt_at = measured_at + timedelta(seconds=1800)
        return self.get(request_id)
