"""Opt-in real Codex video handoff, isolated local media only; incurs SDK usage."""
import argparse
import json
import math
import struct
import wave
import zipfile
from pathlib import Path

from PIL import Image

from creatoros.config import PROJECT_ROOT
from creatoros.integrations.codex import CodexSdkProducer, CODEX_MODEL, CODEX_EFFORT
from creatoros.integrations.producer_skills import ProducerSkillCatalog, _digest
from creatoros.integrations.video_production import produce_video, validate_video_artifact
from creatoros.runs.task_contracts import TaskExecutionSpec, TaskSkill, TaskMaterial
from creatoros.runs.task_execution import file_sha256, freeze_execution_spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--bad-first", action="store_true", help="Ask real Codex to submit a wrong path once, then repair it")
    args = parser.parse_args()
    root = args.directory.resolve()
    if not root.is_relative_to((PROJECT_ROOT / "tmp").resolve()) or root.exists():
        parser.error("Use a fresh directory inside project tmp")
    root.mkdir(parents=True)
    source = root / "source-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: video-delivery-trial\ndescription: 本地2秒视频交付验证\ncreatoros-outputs: [video]\n---\n"
        "只制作2秒MP4，用给定ZIP内的PNG作为静态画面，给定WAV作音轨。"
        "用提供的ffmpeg路径：解压图片，-loop 1 输入PNG，输入WAV，-t 2，"
        "-c:v libx264 -pix_fmt yuv420p -c:a aac -movflags +faststart。"
        "只用现有本地输入，不联网、不下载、不生图、不安装依赖、不调用其他Skill。"
        "所有输出在当前work；最终交付MP4相对路径，标题和一句文案。", encoding="utf-8")
    catalog = ProducerSkillCatalog(root / "catalog", project_root=PROJECT_ROOT)
    record = catalog.register_local(source, role="production")
    picture = root / "frame.png"
    Image.new("RGB", (160, 240), (48, 102, 180)).save(picture)
    archive = root / "frames.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.write(picture, "frame.png")
    audio = root / "tone.wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", int(2000 * math.sin(2 * math.pi * 440 * i / 44100)))
                                   for i in range(88200)))
    spec = TaskExecutionSpec(kind="video", skills=[TaskSkill(skill_id=record["id"],
        digest=_digest(Path(record["local_path"])), role="selected")], materials=[
        TaskMaterial(id="frames", kind="images_zip", path=str(archive), sha256=file_sha256(archive), name="frames.zip"),
        TaskMaterial(id="tone", kind="audio", path=str(audio), sha256=file_sha256(audio), name="tone.wav")])
    run = root / "run"
    spec = freeze_execution_spec(spec, catalog, run / "skill-snapshot")
    report = {"status": "started", "model": CODEX_MODEL, "effort": CODEX_EFFORT,
              "formal_data_written": False, "network_media_download": False}
    def save():
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    print(json.dumps(report), flush=True)
    try:
        producer = CodexSdkProducer(project_root=PROJECT_ROOT, generated_images_root=root / "unused-images",
                                   timeout_seconds=600)
        result = produce_video(producer, directory=run / "revision-001/attempt-001", pack_id="video-trial-r001",
            creator_id="video-trial", series_id="video-series", topic_id="video-topic", topic_title="两秒蓝色画面",
            topic_brief=("用给定图片和WAV制作2秒MP4，按Skill要求交付。" +
                ("本次是交付故障验证：视频只渲染一次。第一次调用 submit_artifacts 时故意把 video_path 填为 missing.mp4，"
                 "收到拒绝后，第二次改成实际已渲染文件路径。不创建 missing.mp4、不复制到这个名字、不重新渲染。"
                 if args.bad_first else "")), series_description="", audience="",
            revision_instruction=None, execution_spec=spec, skills_root=catalog.root)
        checked = validate_video_artifact(result.directory, spec)
        assert 1.9 <= checked.video.duration_seconds <= 2.2
        assert (checked.video.width, checked.video.height) == (160, 240)
        assert checked.video.has_audio
        checkpoint = json.loads((result.directory / "video_checkpoint.json").read_text(encoding="utf-8"))
        assert checkpoint["handoff_protocol"] == "submit-tool-v1" and checkpoint["accepted_sha256"]
        if args.bad_first:
            assert checkpoint["submission_attempts"] == 2, checkpoint
            assert not (result.directory / "work/missing.mp4").exists()
        report.update(submission_attempts=checkpoint["submission_attempts"],
                      accepted_receipt=checkpoint["submission_digest"], bad_first=args.bad_first)
        report.update(status="passed", directory=str(result.directory), thread_id=result.session.thread_id,
                      duration=checked.video.duration_seconds, width=checked.video.width, height=checked.video.height,
                      has_audio=checked.video.has_audio, sha256=checked.video.sha256,
                      usage=result.session.usage.model_dump(), sdk_mock=False, approved=False, published=False)
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        save()
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
