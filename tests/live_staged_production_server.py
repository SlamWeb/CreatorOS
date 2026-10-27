"""Opt-in paid browser probe: real DeepSeek, real Codex, real media; fresh tmp DB."""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import struct
import wave


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--editing-skill", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--resume", action="store_true", help="Restart this explicit test root without reseeding or resubmitting")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    root = args.root.resolve()
    if (root.exists() and not args.resume) or not root.is_relative_to(repo / "tmp"):
        parser.error("Choose a new directory below tmp")
    root.mkdir(parents=True, exist_ok=args.resume)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'studio.db').as_posix()}"
    os.environ["CREATOROS_PRODUCTION_CONCURRENCY"] = "3"
    import uvicorn
    from creatoros.storage import Database, ContentRepository, TopicSource, upgrade_database
    from creatoros.integrations.producer_skills import ProducerSkillCatalog, skills_root_for
    from creatoros.runs import ContentRunService
    from creatoros.web import create_app
    from creatoros.web.server import StudioServer
    upgrade_database(os.environ["DATABASE_URL"])
    db = Database(os.environ["DATABASE_URL"])
    if args.resume:
        if not (root / "setup.json").is_file():
            raise ValueError("Test fixture is incomplete")
        app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"),
                         studio_dist=args.dist.resolve(), chat_root=root / "chats")
        try:
            StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning",
                access_log=False, timeout_graceful_shutdown=3)).run()
        finally:
            db.close()
        return
    catalog = ProducerSkillCatalog(skills_root_for(db))
    image_source = root / "pixel-image"
    image_source.mkdir()
    (image_source / "SKILL.md").write_text(
        "---\nname: staged-pixel-image\ndescription: 清晨到黄昏的极简像素风场景。\ncreatoros-outputs: [image]\n---\n"
        "当前任务只生成一张3:4竖图，白上衣棕发小人，简约像素风，纯净天空，地平线，大量留白。不加文字。"
        "按任务要求改变清晨或黄昏。使用生图模型，完成一张即可，不重复生图。\n", encoding="utf-8")
    image_skill = catalog.register_local(image_source, role="production")
    video_source = root / "editing-skill"
    shutil.copytree(args.editing_skill, video_source, ignore=shutil.ignore_patterns("node_modules", ".git"))
    skill_file = video_source / "SKILL.md"
    original = skill_file.read_text(encoding="utf-8-sig")
    skill_file.write_text(original.replace("---\n", "---\ncreatoros-outputs: [video]\n", 1), encoding="utf-8")
    video_skill = catalog.register_local(video_source, role="production")
    repo_data = ContentRepository(db)
    repo_data.create_creator(creator_id="stages-live", display_name="[真实链路] 图集到视频")
    repo_data.create_series(series_id="stages-series", creator_id="stages-live", name="一天的世界",
                            description="", audience="", skill_name=None)
    repo_data.add_topic(topic_id="stages-topic", series_id="stages-series", title="从清晨到黄昏",
        brief="两张极简像素图片，配音乐组成6秒短视频。", source=TopicSource.MANUAL)
    app = create_app(database=db, run_service=ContentRunService(db, output_root=root / "outputs"),
                     studio_dist=args.dist.resolve(), chat_root=root / "chats")
    for number, skill in enumerate((image_skill, video_skill)):
        app.state.skill_library.add("stages-live", skill["id"], f"live-seed-{number}")
    app.state.skill_library.set_series("stages-series", 0, [image_skill["id"], video_skill["id"]],
        {"image": [{"skill_id": image_skill["id"], "role": "primary"}],
         "video": [{"skill_id": video_skill["id"], "role": "primary"}]})
    music = root / "gentle-beats.wav"
    with wave.open(str(music), "wb") as sound:
        sound.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
        sound.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / 22050)
            * math.exp(-6 * ((i / 22050) % .75)))) for i in range(22050 * 6)))
    material = app.state.materials.put("stages-live", music.name, music.read_bytes())
    (root / "setup.json").write_text(json.dumps(dict(creator_id="stages-live", series_id="stages-series",
        topic_id="stages-topic", image_skill=image_skill, video_skill=video_skill, bgm=material),
        ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        StudioServer(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning",
            access_log=False, timeout_graceful_shutdown=3)).run()
    finally:
        db.close()


if __name__ == "__main__":
    main()
