"""One real DeepSeek Agent → scoped local Studio HTTP → isolated Skill file edit.

Run explicitly with: python -m tests.live_producer_skill_edit --run
This does not use Codex, generate images, or touch the formal Skill database.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime
from io import StringIO
from pathlib import Path

import httpx

from creatoros.agent.loop import run_agent
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.config import PROJECT_ROOT
from creatoros.context import RuntimeContext
from creatoros.integrations.producer_skills import _digest
from creatoros.session.snapshot import load_messages
from creatoros.storage import ContentRepository, CreatorPlatform, Database, Series, upgrade_database
from creatoros.terminal import Console
from creatoros.tools.definitions import tool_registry
from creatoros.web.app import create_app
from tests.agent_studio_support import serve


ALLOWED_TOOLS = frozenset({"list_producer_skills", "get_producer_skill", "update_producer_skill_file"})
TARGET_NAME = "live-edit-probe"
TARGET_DESCRIPTION = "Live Skill editing is connected and verified."
BODY_MARKER = "LIVE_AGENT_EDIT_MARKER: verified"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Make one real DeepSeek request.")
    args = parser.parse_args()
    if not args.run:
        parser.error("live validation requires explicit --run")

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is unavailable; config.py loads the local .env.")

    root = PROJECT_ROOT / "tmp" / f"live-producer-skill-edit-{datetime.now():%Y%m%d-%H%M%S}"
    root.mkdir(parents=True)
    db_url = f"sqlite:///{(root / 'studio.db').as_posix()}"
    upgrade_database(db_url)
    db = Database(db_url)
    provider = DeepSeekProvider(api_key=api_key, timeout_seconds=90, max_retries=0)
    report = {"status": "running", "model": provider.model, "scope": "isolated_account",
              "formal_database_touched": False, "codex_called": False, "image_generation": False}
    try:
        ContentRepository(db).create_creator(
            creator_id="live-edit-account", display_name="Isolated Skill Edit Probe",
            platform=CreatorPlatform.XIAOHONGSHU)
        app = create_app(database=db, chat_root=root / "agent-sessions")
        source = root / "source-skill"
        source.mkdir()
        original_skill = (
            f"---\nname: {TARGET_NAME}\ndescription: Original isolated description.\n---\n\n"
            "# Isolated Skill\n\nOriginal body.\n"
        )
        (source / "SKILL.md").write_text(original_skill, encoding="utf-8", newline="")
        registered = app.state.skill_installs.catalog.register_local(source, role="mind")
        skill_id = registered["id"]
        working = app.state.skill_installs.catalog.root / "working" / skill_id
        original = app.state.skill_installs.catalog.root / "versions" / skill_id
        historical_snapshot = root / "historical-run-snapshot"
        shutil.copytree(working, historical_snapshot)
        original_digest = _digest(original)

        with db.session() as session:
            session.add(Series(
                id="live-edit-series", creator_id="live-edit-account", name="Isolated Skill Probe",
                description="Local isolated verification", audience="Agent tool validation",
                skill_name=skill_id, mind_skill_id=None, production_skill_id=None))

        with serve(app) as base, httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
            session = client.post("/api/agent/sessions", json={"creator_id": "live-edit-account"})
            assert session.status_code == 201, session.text
            session_id = session.json()["id"]
            session_file = root / "agent-sessions" / session_id / "messages.json"
            initial = client.get(f"/api/producer-skills/{skill_id}/files",
                                 headers={"x-creatoros-agent-session": session_id})
            assert initial.status_code == 200, initial.text
            report.update(skill_id=skill_id, session_id=session_id,
                          before_digest=initial.json()["digest"], source_digest=original_digest)

            prompt = (
                f"请修改名为 `{TARGET_NAME}` 的已安装 Skill，只编辑它的根目录 `SKILL.md`。"
                f"Skill ID：{skill_id}。"
                f"将 description 精确改为：{TARGET_DESCRIPTION}；在正文末尾增加一行：{BODY_MARKER}。"
                "请用中文回复。请检查当前文件，然后修改并保存。不要生产内容、安装 Skill 或修改其他文件。"
            )
            (root / "requested-edit.txt").write_text(prompt, encoding="utf-8")
            events = []
            output = StringIO()
            prompts = iter([prompt, "/menu"])
            run_agent(
                provider, max_turns=6,
                console=Console(input_fn=lambda _prompt: next(prompts), output=output),
                on_agent_event=lambda event: events.append({"kind": event.kind, "data": event.data}),
                session_file=session_file,
                runtime_context=RuntimeContext(
                    project_root=PROJECT_ROOT, studio_url=base, allowed_tools=ALLOWED_TOOLS,
                    archive_only_reads=True, creator_id="live-edit-account",
                    agent_session_id=session_id),
            )
            (root / "agent-output.txt").write_text(output.getvalue(), encoding="utf-8")
            messages = load_messages(session_file)
            calls = []
            for message in messages:
                for call in message.get("tool_calls", []):
                    calls.append({"name": call.get("name"),
                                  "arguments": json.loads(call.get("arguments", "{}"))})
            results = [event["data"] for event in events if event["kind"] == "tool_result"]
            usages = [event["data"] for event in events if event["kind"] == "model_usage"]
            report.update(calls=calls, tool_results=[{"name": item.get("name"),
                                                      "is_error": item.get("is_error")}
                                                     for item in results],
                          model_usage=usages)

            names = [call["name"] for call in calls]
            get_calls = [call["arguments"] for call in calls if call["name"] == "get_producer_skill"]
            assert any(call.get("list_files") is True for call in get_calls), get_calls
            assert any(call.get("path") == "SKILL.md" for call in get_calls), get_calls
            update_calls = [call["arguments"] for call in calls
                            if call["name"] == "update_producer_skill_file"]
            assert len(update_calls) == 1, update_calls
            assert not any(item.get("is_error") for item in results), report["tool_results"]
            final_reply = next(m.get("content", "") for m in reversed(messages)
                               if m.get("role") == "assistant" and not m.get("tool_calls"))
            assert re.search(r"[\u4e00-\u9fff]", final_reply), "Expected a Chinese final reply."
            assert not re.search(r"(?i)\b(?:I'll|I've|please confirm|do you confirm)\b", final_reply), final_reply
            commentary = [m.get("content", "") for m in messages
                          if m.get("role") == "assistant" and m.get("tool_calls") and m.get("content")]
            assert all(re.search(r"[\u4e00-\u9fff]", text) for text in commentary), commentary

            current_text = (working / "SKILL.md").read_text(encoding="utf-8")
            current = app.state.skill_installs.catalog.describe(skill_id)
            assert current["description"] == TARGET_DESCRIPTION, current["description"]
            assert BODY_MARKER in current_text, current_text
            assert current["digest"] != report["before_digest"]
            assert _digest(original) == original_digest
            assert (original / "SKILL.md").read_text(encoding="utf-8") == original_skill
            assert (historical_snapshot / "SKILL.md").read_text(encoding="utf-8") == original_skill
            report.update(status="passed", after_digest=current["digest"],
                          description=current["description"], marker_present=True,
                          chinese_reply=True, saved_without_second_confirmation=True,
                          source_unchanged=True, historical_snapshot_unchanged=True)
            (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
            print(f"live_producer_skill_edit=passed evidence={root} tool_calls={names} "
                  f"agent_tokens={sum((item.get('input_tokens') or 0) + (item.get('output_tokens') or 0) for item in usages)}",
                  flush=True)
    except Exception as error:
        report.update(status="failed", failure_type=type(error).__name__)
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
        print(f"live_producer_skill_edit=failed evidence={root} failure_type={type(error).__name__}",
              flush=True)
        raise
    finally:
        provider.client.close()
        db.close()


if __name__ == "__main__":
    main()
