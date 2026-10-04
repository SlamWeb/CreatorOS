"""Account context tree projection and request-only model context smoke."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from sqlalchemy import func, select

from creatoros.agent import loop as agent_loop
from creatoros.agent.loop import build_model_context
from creatoros.ai.context import ContextBudget, ModelContext
from creatoros.ai.types import ModelResponse, StreamEnd, TextDelta, ToolCallDelta
from creatoros.context import RuntimeContext
from creatoros.integrations.producer_skills import ProducerSkillCatalog
from creatoros.session.checkpoint import CompactionCheckpoint
from creatoros.session.context_trace import read_trace, trace_path
from creatoros.session.snapshot import load_messages, save_messages
from creatoros.storage import (ContentRepository, ContentRun, CreatorPlatform,
                               Database, Series, TopicSource, upgrade_database)
from creatoros.tools.results import ToolResult
from creatoros.web.account_context import CreatorContextBuilder
from tests.agent_studio_support import serve
from tests.studio_review_fixtures import make_fixture
from creatoros.terminal import Console


def _skill(source: Path, name: str, description: str) -> Path:
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n"
        "PRIVATE_SKILL_BODY_SENTINEL\n",
        encoding="utf-8",
    )
    (source / "asset.txt").write_text("PRIVATE_ASSET_SENTINEL", encoding="utf-8")
    return source


def _series(database, *, series_id, creator_id, name, skill_name=None,
            mind_skill_id=None, production_skill_id=None, description="short",
            audience="readers", active=True):
    with database.session() as session:
        session.add(Series(
            id=series_id, creator_id=creator_id, name=name,
            description=description, audience=audience,
            skill_name=skill_name, mind_skill_id=mind_skill_id,
            production_skill_id=production_skill_id, is_active=active,
        ))


def _builder_checks(root: Path):
    database_url = f"sqlite:///{(root / 'account-context.db').as_posix()}"
    upgrade_database(database_url)
    database = Database(database_url)
    catalog = ProducerSkillCatalog(root / "producer-skills", project_root=root)
    mind = catalog.register_local(
        _skill(root / "source-mind", "study-mind", "初始 Mind 说明"), role="mind")
    production = catalog.register_local(
        _skill(root / "source-production", "study-production", "Production 说明"), role="production")
    with database.session() as session:
        ContentRepository(database, session=session).create_creator(
            creator_id="creator-a", display_name="账号 A", platform=CreatorPlatform.XIAOHONGSHU,
            account_handle="@a")
        ContentRepository(database, session=session).create_creator(
            creator_id="creator-b", display_name="账号 B")

    _series(database, series_id="single", creator_id="creator-a", name="单 Skill",
            skill_name=mind["id"], description="D" * 1100, audience="A" * 1101)
    _series(database, series_id="pair", creator_id="creator-a", name="组合",
            mind_skill_id=mind["id"], production_skill_id=production["id"])
    _series(database, series_id="broken", creator_id="creator-a", name="坏绑定",
            skill_name="missing-skill-reference")
    _series(database, series_id="inactive", creator_id="creator-a", name="停用栏目",
            skill_name="knowledge-to-carousel", active=False)
    # More than the old directory page size: every account column must remain visible.
    for number in range(105):
        _series(database, series_id=f"many-{number:03d}", creator_id="creator-a",
                name=f"栏目 {number:03d}", skill_name="knowledge-to-carousel")
    _series(database, series_id="other-account", creator_id="creator-b", name="不可见栏目",
            skill_name="knowledge-to-carousel")
    _series(database, series_id="unassigned", creator_id=None, name="未分配栏目",
            skill_name="knowledge-to-carousel")

    builder = CreatorContextBuilder(database, catalog)
    tree = builder.build("creator-a")
    assert tree["kind"] == "creator_context" and tree["as_of"].endswith("+00:00")
    assert tree["creator"]["id"] == "creator-a"
    assert tree["creator"]["display_name"] == "账号 A"
    assert tree["creator"]["platform"] == "xiaohongshu"
    assert tree["creator"]["account_handle"] == "@a"
    assert len(tree["series"]) == 109
    assert {row["id"] for row in tree["series"]}.isdisjoint({"other-account", "unassigned"})
    assert next(row for row in tree["series"] if row["id"] == "inactive")["is_active"] is False
    assert next(row for row in tree["series"] if row["id"] == "pair")["skill_bindings"] == {
        "mind": mind["id"], "production": production["id"]}
    assert next(row for row in tree["series"] if row["id"] == "single")["skill_bindings"] == {
        "single": mind["id"]}
    assert len(tree["skills"]) == 4  # mind, production, missing and shared built-in
    assert len({item["id"] for item in tree["skills"]}) == len(tree["skills"])
    broken = next(item for item in tree["skills"] if item["id"] == "missing-skill-reference")
    assert broken["available"] is False and "PRIVATE" not in json.dumps(broken)
    available = next(item for item in tree["skills"] if item["id"] == mind["id"])
    assert available["available"] is True and available["description"] == "初始 Mind 说明"
    assert "PRIVATE_SKILL_BODY_SENTINEL" not in json.dumps(tree, ensure_ascii=False)
    assert "PRIVATE_ASSET_SENTINEL" not in json.dumps(tree, ensure_ascii=False)
    assert len(next(row for row in tree["series"] if row["id"] == "single")["description"]) <= 1030
    assert any(item["resource"] == "single" and item["field"] == "description"
               for item in tree["omissions"])
    assert any(item["resource"] == "single" and item["field"] == "audience"
               for item in tree["omissions"])

    # A changed local metadata file is read on each build; the Skill body stays private.
    working_skill = Path(catalog.describe(mind["id"])["local_path"]) / "SKILL.md"
    working_skill.write_text(
        "---\nname: study-mind\ndescription: 本地刚编辑的说明\n---\n"
        "PRIVATE_SKILL_BODY_SENTINEL_CHANGED\n",
        encoding="utf-8",
    )
    refreshed = builder.build("creator-a")
    refreshed_mind = next(item for item in refreshed["skills"] if item["id"] == mind["id"])
    assert refreshed_mind["description"] == "本地刚编辑的说明"
    assert refreshed_mind["available"] is True
    assert not (catalog.root / "working" / "missing-skill-reference").exists()
    database.close()


class _CapturingProvider:
    context_window = 100_000
    reserve_output_tokens = 1_000

    def __init__(self):
        self.contexts = []
        self.calls = 0

    def stream(self, context):
        self.contexts.append(context)
        self.calls += 1
        if self.calls == 1:
            yield ToolCallDelta(0, "write-1", "compose_series", "{}")
            yield StreamEnd("tool_calls")
        else:
            yield TextDelta("done")
            yield StreamEnd("stop")


def _model_context_and_trace_checks(root: Path):
    messages = [
        {"role": "system", "content": "stable prefix"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "recent request"},
    ]
    original = deepcopy(messages)
    checkpoint = CompactionCheckpoint.create(
        summary="rolling summary", messages=messages, first_retained_index=3,
        tokens_before=500_000)
    tree = {"kind": "creator_context", "creator": {"id": "creator-a"},
            "series": [{"id": "series-a", "description": "x" * 4000}],
            "skills": [], "omissions": []}
    context = build_model_context(messages, [], checkpoint, context_data=tree)
    request, _ = context.to_request()
    assert request[0] == original[0]
    assert "rolling summary" in request[1]["content"]
    assert "creator-a" in request[2]["content"]
    assert "recent request" in request[3]["content"]
    assert messages == original
    assert "creator-a" not in checkpoint.summary and "series-a" not in checkpoint.summary
    assert "old request" not in str(request)
    assert ContextBudget.from_context(context).estimated_input_tokens > ContextBudget.from_context(
        build_model_context(messages, [], checkpoint)).estimated_input_tokens
    assert build_model_context(messages, []).to_request() == ModelContext.from_messages(messages, []).to_request()

    provider = _CapturingProvider()
    session_file = root / "messages.json"
    save_messages([{"role": "system", "content": "stable prefix"}], session_file)
    snapshots = []

    def context_factory():
        value = {"kind": "creator_context", "creator": {"id": "creator-a"},
                 "revision": len(snapshots) + 1}
        snapshots.append(deepcopy(value))
        return value

    original_execute = agent_loop.execute_tool_call
    agent_loop.execute_tool_call = lambda *args, **kwargs: ToolResult("saved")
    try:
        inputs = iter(["write", "/exit"])
        agent_loop.run_agent(
            provider,
            console=Console(input_fn=lambda _prompt: next(inputs), output=StringIO()),
            session_file=session_file,
            context_factory=context_factory,
        )
    finally:
        agent_loop.execute_tool_call = original_execute

    assert len(snapshots) == 2  # once for the query, then after successful business write
    first, second = [ctx.to_request()[0] for ctx in provider.contexts]
    assert '"revision":1' in first[1]["content"]
    assert '"revision":2' in second[1]["content"]
    ledger = load_messages(session_file)
    assert "creator_context" not in json.dumps(ledger, ensure_ascii=False)
    assert "revision" not in json.dumps(ledger, ensure_ascii=False)
    finished = [row for row in read_trace(session_file, limit=100)["items"]
                if row["event"] == "finished" and row["request_kind"] == "main"]
    assert len(finished) == 2
    for row, expected in zip(finished, snapshots):
        assert row["account_context"] == expected
        assert row["estimated_parts"]["account_context"] > 0
        assert sum(row["estimated_parts"].values()) == row["estimated_input_tokens"]
        assert row["estimated_input_tokens"] == ContextBudget.from_context(
            provider.contexts[finished.index(row)]).estimated_input_tokens
    assert trace_path(session_file).exists()


class _CompactingProvider(_CapturingProvider):
    context_window = 6_000
    reserve_output_tokens = 500

    def __init__(self):
        super().__init__()
        self.summary_contexts = []

    def complete(self, context):
        self.summary_contexts.append(context)
        markdown = """## Goal
none
## Constraints & Preferences
none
## Progress
### Done
none
### In Progress
none
### Blocked
none
## Key Decisions
none
## Important Facts & IDs
none
## Files & Artifacts
none
## Next Steps
none
## Unresolved Questions
none"""
        return ModelResponse(markdown, [])

    def stream(self, context):
        self.contexts.append(context)
        yield TextDelta("done")
        yield StreamEnd("stop")


def _compaction_checks(root: Path):
    # A large prior tool result forces summary planning. The account tree must be
    # excluded from that summary input and restored in the subsequent main request.
    session_file = root / "compaction-messages.json"
    history = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "large-result", "name": "list_creators", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "large-result", "content": "x" * 30_000},
        {"role": "user", "content": "recent request"},
        {"role": "assistant", "content": "recent answer"},
    ]
    save_messages(history, session_file)
    provider = _CompactingProvider()
    inputs = iter(["continue", "/exit"])
    tree = {"kind": "creator_context", "creator": {"id": "creator-a"},
            "revision": 1, "series": [], "skills": [], "omissions": []}
    agent_loop.run_agent(
        provider,
        console=Console(input_fn=lambda _prompt: next(inputs), output=StringIO()),
        session_file=session_file,
        runtime_context=RuntimeContext(root, allowed_tools=frozenset()),
        context_factory=lambda: deepcopy(tree),
    )
    assert len(provider.summary_contexts) == 1
    summary_request = provider.summary_contexts[0].to_request()[0]
    assert "creator-a" not in json.dumps(summary_request, ensure_ascii=False)
    assert provider.contexts, read_trace(session_file, limit=100)
    main_request = provider.contexts[0].to_request()[0]
    assert "creator-a" in json.dumps(main_request, ensure_ascii=False)
    checkpoint = agent_loop.load_compaction_checkpoint(load_messages(session_file), session_file)
    assert checkpoint is not None
    assert "creator-a" not in checkpoint.summary
    trace_rows = [row for row in read_trace(session_file, limit=100)["items"]
                  if row["event"] == "finished"]
    compact = next(row for row in trace_rows if row["request_kind"] == "compaction")
    main = next(row for row in trace_rows if row["request_kind"] == "main")
    assert "account_context" not in compact["estimated_parts"]
    assert main["account_context"]["creator"]["id"] == "creator-a"

    # A dynamic tree larger than the whole input budget blocks before any model call.
    blocked_file = root / "blocked-messages.json"
    blocked_provider = _CompactingProvider()
    blocked_provider.context_window = 1_000
    blocked_provider.reserve_output_tokens = 100
    giant = {"kind": "creator_context", "creator": {"id": "creator-a"},
             "blob": "z" * 8_000}
    blocked_inputs = iter(["do work", "/exit"])
    agent_loop.run_agent(
        blocked_provider,
        console=Console(input_fn=lambda _prompt: next(blocked_inputs), output=StringIO()),
        session_file=blocked_file,
        runtime_context=RuntimeContext(root, allowed_tools=frozenset()),
        context_factory=lambda: deepcopy(giant),
    )
    assert blocked_provider.contexts == [] and blocked_provider.summary_contexts == []
    blocked = [row for row in read_trace(blocked_file, limit=100)["items"]
               if row["event"] == "finished" and row["request_kind"] == "main"]
    assert len(blocked) == 1 and blocked[0]["status"] == "blocked"
    assert blocked[0]["sent"] is False
    assert blocked[0]["estimated_parts"]["account_context"] > 0


def _missing_skill_run_guard(root: Path):
    database, runs, producer, app = make_fixture(root / "run-guard")
    try:
        catalog = app.state.skill_installs.catalog
        registered = catalog.register_local(
            _skill(root / "run-guard" / "source-skill", "guarded-skill", "绑定 Skill 元数据"),
            role="production",
        )
        local_path = Path(catalog.describe(registered["id"])["local_path"])
        repository = ContentRepository(database)
        repository.create_series(
            series_id="guarded-series", creator_id="review-lab", name="需验证 Skill 的栏目",
            description="用于本地生产门禁回归", audience="隔离测试", skill_name=registered["id"],
        )
        repository.add_topic(topic_id="guarded-topic", series_id="guarded-series",
                             title="不可创建 Run", source=TopicSource.MANUAL)
        (local_path / "SKILL.md").unlink()
        session_id = app.state.chat.create("review-lab")["id"]
        headers = {"x-creatoros-agent-session": session_id}
        with serve(app) as base, httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
            assert client.get("/api/creators/review-lab", headers=headers).status_code == 200
            with database.session() as session:
                count_before = session.scalar(select(func.count()).select_from(ContentRun)) or 0
            response = client.post("/api/runs", headers=headers, json={"topic_id": "guarded-topic"})
            assert response.status_code == 409, response.text
            assert "未创建新任务" in response.text, response.text
            assert not (local_path / "SKILL.md").exists()  # no automatic restoration
            with database.session() as session:
                assert (session.scalar(select(func.count()).select_from(ContentRun)) or 0) == count_before
            assert producer.calls == 0
    finally:
        database.close()


def main():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        _builder_checks(root)
        _model_context_and_trace_checks(root)
        _compaction_checks(root)
        _missing_skill_run_guard(root)
    print("account_context_smoke=passed builder=isolated metadata=refreshed model_context=request_only trace=account_context")


if __name__ == "__main__":
    main()
