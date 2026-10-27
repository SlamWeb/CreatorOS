"""Explicitly controlled browser fixtures; real MP4 bytes, no Codex/model claim."""
from pathlib import Path
import base64
import math
import shutil
import struct
import subprocess
import wave


def seed_staged_video(catalog, directory):
    from creatoros.integrations.media_tools import media_tool
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    skill = directory / "skill"
    skill.mkdir(exist_ok=True)
    (skill / "SKILL.md").write_text("---\nname: staged-ui-video\ndescription: Controlled browser video test\ncreatoros-outputs: [video]\n---\nRender a short clip from the supplied images and BGM.\n", encoding="utf-8")
    record = catalog.register_local(skill, role="production")
    image_skill = directory / "image-skill"
    image_skill.mkdir(exist_ok=True)
    (image_skill / "SKILL.md").write_text("---\nname: staged-ui-image\ndescription: Controlled image task selection\ncreatoros-outputs: [image]\n---\nRender supplied story as images.\n", encoding="utf-8")
    image_record = catalog.register_local(image_skill, role="production")
    audio = directory / "music.wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", int(4000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(8000)))
    clip = directory / "preview.mp4"
    if not clip.exists():
        raw = directory / "frames.rgb"
        raw.write_bytes(bytes([48, 67, 99]) * 240 * 320 * 10)
        subprocess.run([media_tool("ffmpeg"), "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "240x320", "-framerate", "10", "-i", str(raw), "-i", str(audio), "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
    return {"skill": record, "image_skill": image_record, "audio_base64": base64.b64encode(audio.read_bytes()).decode()}


def produce_controlled_video(request, directory):
    from types import SimpleNamespace
    from unittest.mock import patch
    from creatoros.integrations.codex import CodexUsage
    from creatoros.integrations.video_production import (VIDEO_CHECKPOINT, VideoCheckpoint, VideoDelivery, _identity, produce_video)
    from creatoros.integrations.visual_production import atomic_json

    async def controlled_transport(producer, target, spec, payload, checkpoint, inputs):
        thread_id = inputs.get("thread_id") or "controlled-staged-ui-video"
        inputs["on_thread_started"](thread_id)
        shutil.copyfile(Path(directory) / "preview.mp4", target / "work" / "finished.mp4")
        saved = VideoCheckpoint(input_digest=_identity(spec, payload), thread_id=thread_id, turn_completed=True,
            response=VideoDelivery(title=payload["topic_title"], text="[受控浏览器夹具] 验证播放器与验收，不代表模型制作。", video_path="finished.mp4", complete=True).model_dump_json(), usage=CodexUsage())
        atomic_json(target / VIDEO_CHECKPOINT, saved)
        return saved

    with patch("creatoros.integrations.video_production._execute_video", controlled_transport):
        return produce_video(SimpleNamespace(native_skill_inputs=True), **request)
