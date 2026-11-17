"""Media inspection shared by input validation and rendered video acceptance."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess


def media_tool(name):
    value = os.getenv(f"CREATOROS_{name.upper()}") or shutil.which(name)
    if not value or not Path(value).is_file():
        raise ValueError(f"缺少 {name}，请配置 CREATOROS_{name.upper()} 或加入 PATH。")
    return str(Path(value).resolve())


def probe_media(path, *, input_options=(), audio_duration=False):
    result = subprocess.run([media_tool("ffprobe"), "-v", "error", "-show_format", "-show_streams",
                             "-of", "json", *input_options, str(path)], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("无法读取媒体文件，请上传有效的音频或视频。")
    value = json.loads(result.stdout)
    streams = value.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not
                  s.get("disposition", {}).get("attached_pic")), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fallback = value.get("format", {}).get("duration", 0)
    duration = audio.get("duration") if audio_duration else None
    duration = float(fallback if duration in (None, "N/A") else duration)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("媒体没有有效时长。")
    return dict(duration_seconds=duration, width=video.get("width", 0), height=video.get("height", 0),
                video_codec=video.get("codec_name"),
                audio_codec=next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), None),
                has_audio=any(s.get("codec_type") == "audio" for s in streams), has_video=bool(video),
                format=value.get("format", {}).get("format_name", ""))


def decode_media(path, *, input_options=(), audio_only=False):
    output_options = ["-map", "0:a:0", "-vn"] if audio_only else ["-c:v", "rawvideo"]
    result = subprocess.run([media_tool("ffmpeg"), "-v", "error", "-xerror", *input_options, "-i", str(path),
                             *output_options, "-f", "null", "-"], capture_output=True, timeout=300)
    if result.returncode:
        raise ValueError("媒体完整解码失败，文件可能损坏。")
