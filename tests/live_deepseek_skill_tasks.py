"""Opt-in DeepSeek probe. The only external calls are two isolated account turns."""
import argparse
import base64
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from time import monotonic, sleep
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--scenario", choices=("copy", "stitch", "both"), default="both")
    args = parser.parse_args()
    if not os.getenv("DEEPSEEK_API_KEY"):
        parser.error("Set DEEPSEEK_API_KEY in the process environment.")
    root = Path(tempfile.mkdtemp(prefix="creatoros-live-deepseek-skills-"))
    print(f"Isolated evidence: {root}", flush=True)
    url = "sqlite:///" + (root / "probe.sqlite").as_posix()
    os.environ.update(DATABASE_URL=url, PYTHON_DOTENV_DISABLED="1",
                      CREATOROS_EVAL_REGISTRY=str(root / "eval.sqlite"))

    import httpx
    from PIL import Image, ImageDraw
    from sqlalchemy import text
    from creatoros.integrations.producer_skills import ProducerSkillCatalog, SkillInstallService
    from creatoros.integrations.skill_extraction import SkillExtractionService
    from creatoros.integrations.topic_research import TopicResearchService
    from creatoros.runs import ContentRunService
    from creatoros.storage import ContentRepository, Database, upgrade_database
    from creatoros.web.app import create_app
    from tests.agent_studio_support import serve

    def denied(*args, **kwargs):
        raise AssertionError("Unexpected non-DeepSeek external action")

    class Denied:
        __getattr__ = lambda self, name: denied

    def install(library, name, display, description, body):
        source = root / "source" / name
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            f'---\nname: {name}\ncreatoros-display-name: "{display}"\ndescription: "{description}"\n---\n\n{body}\n',
            encoding="utf-8")
        skill = library.catalog.register_local(source, role="legacy_end_to_end")
        library.add("lab", skill["id"], str(uuid4()))
        return skill["id"]

    def post(client, path, data):
        r = client.post(path, json=data)
        r.raise_for_status()
        return r.json()

    def run_turn(client, prompt, attachments=(), workspace=False):
        chat = post(client, "/api/agent/sessions", {"creator_id": "lab"})
        route = "/api/agent/sessions/" + chat["id"]
        ids = [post(client, route + "/attachments", {"name": name, "data_base64": base64.b64encode(raw).decode()})["id"]
               for name, raw in attachments]
        turn = str(uuid4())
        post(client, route + "/turns",
             {"request_id": turn, "expected_version": chat["version"], "text": prompt,
              "attachment_ids": ids, "reasoning_effort": "high", "skill_workspace": workspace})
        deadline = monotonic() + 330
        while monotonic() < deadline:
            response = client.get(route)
            response.raise_for_status()
            view = response.json()
            if view["status"] != "running":
                (root / ("stitch-turn.json" if workspace else "copy-turn.json")).write_text(
                    json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
                assert view["status"] == "idle" and not view.get("error"), view.get("error")
                return chat["id"], turn, view["entries"]
            sleep(0.6)
        raise TimeoutError("DeepSeek turn did not finish on the isolated server.")

    upgrade_database(url)
    with closing(Database(url)) as db:
        ContentRepository(db).create_creator(creator_id="lab", display_name="[隔离实测] Skill 研究室")
        catalog = ProducerSkillCatalog(root / "catalog")
        app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "runs", producer_factory=denied),
                         chat_root=root / "chats", skill_install_service=SkillInstallService(catalog, installer=Denied()),
                         skill_extraction_service=SkillExtractionService(catalog, extractor=denied, reviser=denied, producer_factory=denied),
                         topic_research_service=TopicResearchService(db, catalog, researcher=Denied()),
                         publication_client=Denied(), eval_root=root / "eval")
        library = app.state.chat.skill_tasks.library
        copy_id = install(library, "interview-voice", "慢半拍口播", "用口语写实用建议的短口播",
            "开头先说原句「我先把这件事放到桌面上。」然后用一个具体小场景，再只给一个能立刻试的小动作。"
            "不要小标题，不要提写作规则，不写万能金句。结尾原句「先试一回，剩下的以后再说。」")
        stitch_id = install(library, "four-panel-layout", "四图拼接规范", "把四张原图无缩放按 2×2 接成 PNG",
            "左上是第一张，右上第二张，左下第三张，右下第四张。"
            "原始像素不缩放、不裁切、不加边距，PNG 文件名是 stitched.png。"
            "任务 Python 环境的 PyPNG 模块名为 png，可用 png.Reader 和 png.Writer 读取和写入像素。")
        result = {}
        with serve(app) as base, httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
            if args.scenario in ("copy", "both"):
                _, _, entries = run_turn(client, "用账号里已登记的「慢半拍口播」Skill 写一段短口播。"
                    "主题是下班后总想刷半小时短视频，怎样先做一个小动作再决定刷不刷。只交正文。")
                answer = "\n".join(e.get("text", "") for e in entries if e["kind"] == "assistant")
                calls = [e["name"] for e in entries if e["kind"] == "tool"]
                assert "get_producer_skill" in calls and "manage_skill_task" not in calls, calls
                assert "我先把这件事放到桌面上。" in answer and "先试一回，剩下的以后再说。" in answer, answer
                (root / "voiceover.txt").write_text(answer, encoding="utf-8")
                result["copy"] = {"skill": copy_id, "tool_calls": calls, "output": "voiceover.txt"}
                print("Voiceover verified", flush=True)
            if args.scenario in ("stitch", "both"):
                images = []
                for index, color in enumerate([(120, 36, 25), (26, 115, 53), (24, 45, 120), (180, 150, 24)], 1):
                    buf = io.BytesIO()
                    picture = Image.new("RGB", (512, 512), color)
                    ImageDraw.Draw(picture).text((56, 64), f"Tile {index}", fill="white")
                    picture.save(buf, "PNG")
                    images.append((f"tile-{index}.png", buf.getvalue()))
                sid, tid, entries = run_turn(client, "用账号里「四图拼接规范」Skill 把我这条消息的四张图交付为一个文件。"
                    "在任务目录编写并运行 Python 脚本完成，验证尺寸后交付可下载的实际 PNG。", images, True)
                expected = f"/api/agent/sessions/{sid}/skill-tasks/{tid}/download"
                links = [link["url"] for e in entries for link in e.get("links", []) if link["url"].startswith(expected)]
                assert links, "Missing host-owned delivery receipt"
                fetched = client.get(links[0])
                fetched.raise_for_status()
                with Image.open(io.BytesIO(fetched.content)) as joined:
                    assert joined.size == (1024, 1024)
                    for index, (_, raw) in enumerate(images):
                        x, y = index % 2 * 512, index // 2 * 512
                        with Image.open(io.BytesIO(raw)) as original:
                            assert joined.crop((x, y, x+512, y+512)).convert("RGB").tobytes() == original.tobytes()
                (root / "stitched.png").write_bytes(fetched.content)
                workspace = app.state.chat.skill_tasks.store.open(sid, tid)
                scripts = [item["path"] for item in workspace.list_files()
                           if item["path"].startswith("work/") and item["path"].endswith(".py")]
                assert scripts and any(item["skill_id"] == stitch_id for item in workspace.manifest["skills"])
                result["stitch"] = {"skill": stitch_id, "scripts": scripts, "output": "stitched.png",
                    "width": 1024, "height": 1024, "sha256": hashlib.sha256(fetched.content).hexdigest(),
                    "quadrants_exact": True}
                print("Stitch verified", flush=True)
        with db.engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM content_runs")).scalar_one() == 0
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
