"""Real isolated SQLite + loopback HTTP queue receipt/regression checks.

No model calls. Only the lost-response case injects a transport exception after
the real server has committed. Runtime wiring uses a controlled local provider;
it is a deterministic regression, not a model quality/E2E score.
"""
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import func, select, text

from creatoros.agent.loop import run_agent
from creatoros.ai.types import ModelResponse, StreamEnd, TextDelta, ToolCall, ToolCallDelta
from creatoros.context import RuntimeContext
from creatoros.integrations.studio import StudioClient, StudioClientError
from creatoros.operations.models import OperationPlan
from creatoros.operations.service import PendingOperationError, PendingOperationService
from creatoros.runs import ContentRunService
from creatoros.storage import ContentRepository, Database, Series, Topic, WriteReceipt, upgrade_database
from creatoros.terminal import Console
from creatoros.tools import execute_tool_call
from creatoros.tools.studio import _queue_identity
from creatoros.web.app import create_app
from tests.agent_studio_support import serve


def counts(db):
    with db.session() as session:
        return tuple(int(session.scalar(select(func.count()).select_from(model)))
                     for model in (Topic, WriteReceipt))


def call(context, series="series-a", title="常见词义", **kwargs):
    args = {"series_id": series, "topics": [{"title": title}], **kwargs}
    return execute_tool_call(ToolCall(uuid4().hex, "queue_topics", json.dumps(args)),
                             context=context, model_requested=True)


class RepeatingProvider:
    """Controlled duplicate call fault; never sends requests to a model service."""
    def __init__(self):
        self.calls = 0

    def complete(self, context):
        self.calls += 1
        if self.calls == 1:
            args = json.dumps({"series_id": "series-a", "topics": [{"title": "Runtime 重复动作"}]})
            return ModelResponse(None, [ToolCall("first", "queue_topics", args),
                                        ToolCall("second", "queue_topics", args)])
        return ModelResponse("已入队。", [])

    def stream(self, context):
        response = self.complete(context)
        if response.content:
            yield TextDelta(response.content)
        for index, call in enumerate(response.tool_calls):
            yield ToolCallDelta(index, call.id, call.name, call.arguments)
        yield StreamEnd("tool_calls" if response.tool_calls else "stop")


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        database_url = f"sqlite:///{(root / 'queue.db').as_posix()}"
        upgrade_database(database_url)
        db = Database(database_url)
        repo = ContentRepository(db)
        for identity in ("a", "b"):
            repo.create_creator(creator_id=f"creator-{identity}", display_name=identity)
        for identity, owner in (("a", "a"), ("a2", "a"), ("b", "b")):
            repo.create_series(series_id=f"series-{identity}", creator_id=f"creator-{owner}",
                               name=identity, description="fixture", audience="fixture",
                               skill_name="knowledge-to-carousel")
        chat_root = root / "chat"
        app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"),
                         chat_root=chat_root, eval_root=root / "eval")
        context = None
        try:
            with serve(app) as base, httpx.Client(base_url=base, trust_env=False) as client:
                session_a = app.state.chat.create("creator-a")["id"]
                session_b = app.state.chat.create("creator-b")["id"]
                headers_a = {"x-creatoros-agent-session": session_a}
                headers_b = {"x-creatoros-agent-session": session_b}
                context = RuntimeContext(project_root=root, studio_url=base,
                    session_file=chat_root / session_a / "messages.json", agent_session_id=session_a,
                    creator_id="creator-a", user_request_id=str(uuid4()),
                    archive_only_reads=True, allowed_tools=frozenset({"queue_topics"}))
                first = call(context)
                assert not first.is_error, first.content
                initial = json.loads(first.content)
                assert counts(db) == (1, 1)
                # Different tool-call IDs, JSON key order, default source and audit text are one action.
                again = call(context, topics=[{"source": "manual", "title": " 常见词义 ", "brief": None}],
                             summary="不同审计摘要不代表新动作")
                replay = json.loads(again.content)
                assert not again.is_error and replay["deduplicated"] is True, again.content
                assert replay["request_id"] == initial["request_id"]
                assert replay["topic_ids"] == initial["topic_ids"]
                assert counts(db) == (1, 1)
                current = client.get("/api/series/series-a/topics").json()["items"]
                assert initial["topic_ids"] == [item["id"] for item in current]

                changed = call(context, title="不同动作")
                assert not changed.is_error and counts(db) == (2, 2), changed.content
                other_series = call(context, series="series-a2")
                assert not other_series.is_error and counts(db) == (3, 3), other_series.content
                next_turn = call(replace(context, user_request_id=str(uuid4())))
                assert not next_turn.is_error and counts(db) == (4, 4), next_turn.content
                assert json.loads(next_turn.content)["request_id"] != initial["request_id"]
                # Same title is intentionally legal on a new explicit user turn.
                assert len([t for t in repo.list_topics("series-a") if t.title == "常见词义"]) == 2

                # The server itself rejects same-key/body and resource collisions, regardless of adapter.
                payload = {"request_id": initial["request_id"], "topics": [{"title": "常见词义"}]}
                direct_replay = client.post("/api/series/series-a/queue", json=payload)
                assert direct_replay.status_code == 201
                assert direct_replay.json()["topic_ids"] == initial["topic_ids"]
                for path, request in (("series-a", {**payload, "topics": [{"title": "异文冲突"}]}),
                                      ("series-a2", payload), ("series-b", payload)):
                    denied = client.post(f"/api/series/{path}/queue", json=request)
                    assert denied.status_code == 409, denied.text
                assert counts(db) == (4, 4)
                assert client.post("/api/series/series-b/queue", json=payload,
                                   headers=headers_b).status_code == 403
                assert client.get(f"/api/series/series-b/queue/receipts/{initial['request_id']}",
                                  headers=headers_b).status_code == 403
                assert client.get(f"/api/series/series-a2/queue/receipts/{initial['request_id']}",
                                  headers=headers_a).status_code == 403

                absent_id = uuid4().hex
                missing = client.get(f"/api/series/series-a/queue/receipts/{absent_id}", headers=headers_a)
                assert missing.status_code == 200 and missing.json()["status"] == "unknown"
                assert missing.headers["cache-control"] == "no-store"
                assert counts(db) == (4, 4)

                # A transport response is lost after the real transaction succeeded.
                lost_context = replace(context, user_request_id=str(uuid4()))
                actual_request = StudioClient.request
                calls = []
                def drop_response(self, method, path, **options):
                    calls.append(method)
                    result = actual_request(self, method, path, **options)
                    if method == "POST" and path.endswith("/queue"):
                        raise StudioClientError("注入：提交成功后的响应丢失", "studio_outcome_unknown")
                    return result
                with patch.object(StudioClient, "request", drop_response):
                    lost = call(lost_context, title="响应丢失")
                    assert lost.is_error and lost.error_type == "studio_outcome_unknown", lost.content
                    recovered = call(lost_context, title="响应丢失")
                    assert not recovered.is_error and json.loads(recovered.content)["deduplicated"], recovered.content
                assert calls == ["POST", "GET"] and counts(db) == (5, 5)

                # Even if no receipt exists, an attempted marker never starts another POST.
                uncertain = replace(context, user_request_id=str(uuid4()))
                _, marker = _queue_identity("series-a", [{"title": "未确定", "brief": None, "source": "manual"}], uncertain)
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("", encoding="utf-8")  # Simulates interruption before response/marker body completion.
                checked = call(uncertain, title="未确定")
                assert checked.is_error and checked.error_type == "studio_outcome_unknown", checked.content
                assert counts(db) == (5, 5)

                # A real SQLite constraint fault rolls the entire batch back; repeat is only a read.
                failed_context = replace(context, user_request_id=str(uuid4()))
                with db.session() as session:
                    session.execute(text("CREATE TRIGGER block_queue_fixture BEFORE INSERT ON topics "
                                         "WHEN NEW.title = '事务拒绝' BEGIN SELECT RAISE(ABORT, 'fixture'); END"))
                failure_calls = []
                def record_failure(self, method, path, **options):
                    if "/queue" in path:
                        failure_calls.append(method)
                    return actual_request(self, method, path, **options)
                batch = [{"title": "不应半成功"}, {"title": "事务拒绝"}]
                with patch.object(StudioClient, "request", record_failure):
                    failure = call(failed_context, topics=batch)
                    assert failure.is_error, failure.content
                    observed = call(failed_context, topics=batch)
                    assert observed.is_error, observed.content
                assert failure_calls == ["POST", "GET"] and counts(db) == (5, 5)
                with db.session() as session:
                    session.execute(text("DROP TRIGGER block_queue_fixture"))

                # Legacy receipts lack a frozen account: scoped replay fails closed, local replay remains compatible.
                with db.session() as session:
                    receipt = session.get(WriteReceipt, initial["request_id"])
                    receipt.response_json = {k: v for k, v in receipt.response_json.items() if k != "creator_id"}
                assert client.get(f"/api/series/series-a/queue/receipts/{initial['request_id']}",
                                  headers=headers_a).status_code == 403
                assert client.get(f"/api/series/series-a/queue/receipts/{initial['request_id']}").status_code == 200
                with db.session() as session:
                    receipt = session.get(WriteReceipt, initial["request_id"])
                    receipt.response_json = {**receipt.response_json, "creator_id": "creator-a"}
                assert counts(db) == (5, 5)

                # A legacy receipt without allocation mode remains queryable; guessed-ID POST replay is refused.
                with db.session() as session:
                    receipt = session.get(WriteReceipt, initial["request_id"])
                    receipt.response_json = {k: v for k, v in receipt.response_json.items() if k != "generated_topic_ids"}
                assert client.post("/api/series/series-a/queue", json=payload).status_code == 409
                assert client.get(f"/api/series/series-a/queue/receipts/{initial['request_id']}").status_code == 200
                with db.session() as session:
                    receipt = session.get(WriteReceipt, initial["request_id"])
                    receipt.response_json = {**receipt.response_json, "generated_topic_ids": True}
                assert counts(db) == (5, 5)

                # Runtime supplies the host turn key to two separate model tool-call IDs.
                prompts = iter(["入队一次", "/exit"])
                runtime_calls = []
                def record_request(self, method, path, **options):
                    if "/queue" in path:
                        runtime_calls.append(method)
                    return actual_request(self, method, path, **options)
                with patch.object(StudioClient, "request", record_request):
                    run_agent(RepeatingProvider(), console=Console(input_fn=lambda _: next(prompts), output=StringIO()),
                              session_file=root / "runtime.json", runtime_context=context,
                              user_request_id=str(uuid4()))
                assert counts(db) == (6, 6)
                assert runtime_calls == ["POST", "GET"]

                # Frozen receipt ownership is still enforced after a column changes accounts.
                foreign_id = json.loads(other_series.content)["request_id"]
                with db.session() as session:
                    session.get(Series, "series-a2").creator_id = "creator-b"
                assert client.get(f"/api/series/series-a2/queue/receipts/{foreign_id}",
                                  headers=headers_b).status_code == 403
                assert client.post("/api/series/series-a2/queue", json={"request_id": foreign_id,
                    "topics": [{"title": "常见词义"}]}, headers=headers_b).status_code == 403
                assert counts(db) == (6, 6)

            # A fresh server/RuntimeContext reads the persisted marker+receipt, not process-local memory.
            restored_app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"),
                                      chat_root=chat_root, eval_root=root / "eval")
            with serve(restored_app) as new_base:
                restored = call(replace(context, studio_url=new_base))
                assert not restored.is_error and json.loads(restored.content)["deduplicated"]
                assert json.loads(restored.content)["topic_ids"] == initial["topic_ids"]
                assert counts(db) == (6, 6)
                # The same service is used for research candidates: their stable IDs are part of intent.
                service = PendingOperationService(db, parser=None)
                stable_id = "research-" + "f" * 32 + "-c1"
                plan = OperationPlan.model_validate({"operations": [{"action": "add_topics",
                    "series_id": "series-a", "topics": [{"topic_id": stable_id,
                        "title": "相同研究标题", "brief": "相同研究切入点", "source": "research"}]}]})
                candidate_key = uuid4().hex
                _, duplicate = service.execute_direct("研究选题", plan, scope_series_id="series-a",
                                                       request_id=candidate_key, origin="agent")
                assert not duplicate and counts(db) == (7, 7)
                _, duplicate = service.execute_direct("研究选题", plan, scope_series_id="series-a",
                                                       request_id=candidate_key, origin="agent")
                assert duplicate and counts(db) == (7, 7)
                changed_plan = plan.model_dump(mode="json")
                changed_plan["operations"][0]["topics"][0]["topic_id"] = "research-" + "f" * 32 + "-c2"
                for changed, mode in ((OperationPlan.model_validate(changed_plan), False), (plan, True)):
                    try:
                        service.execute_direct("研究选题", changed, scope_series_id="series-a",
                                               request_id=candidate_key, origin="agent", generated_topic_ids=mode)
                    except PendingOperationError:
                        pass
                    else:
                        raise AssertionError("Different candidate identity or queue mode reused a receipt")
                assert counts(db) == (7, 7)
                with httpx.Client(base_url=new_base, trust_env=False) as client:
                    collision = client.post("/api/series/series-a/queue", json={"request_id": candidate_key,
                        "topics": [{"title": "相同研究标题", "brief": "相同研究切入点", "source": "research"}]})
                    assert collision.status_code == 409 and "另一种入队方式" in collision.text
                # Legacy deterministic IDs can still replay only their exact stored plan.
                with db.session() as session:
                    receipt = session.get(WriteReceipt, candidate_key)
                    receipt.response_json = {k: v for k, v in receipt.response_json.items() if k != "generated_topic_ids"}
                _, duplicate = service.execute_direct("研究选题", plan, scope_series_id="series-a",
                                                       request_id=candidate_key, origin="agent")
                assert duplicate and counts(db) == (7, 7)
        finally:
            db.close()
    print("queue_idempotency_smoke=passed stable_turn=passed replay_ids=passed collisions=blocked unknown=read_only restart=passed")


if __name__ == "__main__":
    main()
