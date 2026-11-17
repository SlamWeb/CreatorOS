"""Run the installed whole-track analyzer and validate its immutable handoff."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import psutil


SCHEMA = "bgm-editing-brief-v2"
MAX_ANALYSIS_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 600
ANALYSIS_TIMEOUT_SECONDS = 1800


def read_analysis(path):
    if not path.is_file() or path.stat().st_size > MAX_ANALYSIS_BYTES:
        raise ValueError("分析结果缺失或超过大小限制。")
    return path.read_bytes()


def analyzer_version(skill):
    digest = hashlib.sha256()
    for path in sorted([skill / "runtime.lock.json", *(skill / "scripts").glob("*.py")]):
        if path.is_file():
            digest.update(path.name.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return "bgm-understanding:" + digest.hexdigest()


def full_summary(brief, checksum):
    audio = brief["audio"]
    duration = audio["duration_seconds"]
    cut_points = brief["cut_points"]
    if (brief.get("schema") != SCHEMA or brief.get("status") != "complete" or
            audio.get("source_sha256") != checksum or audio.get("source_start_seconds") != 0 or
            not isinstance(duration, (float, int)) or not math.isfinite(duration) or
            not 0 < duration <= MAX_SECONDS + .01 or cut_points.get("onset_coverage") != "full_detected_events" or
            any(not isinstance(cut_points.get(key), list) for key in ("beats", "bar_starts", "onsets"))):
        raise ValueError("分析结果未完整覆盖同一音频的全曲和完整卡点。")
    overview = brief["overview"]
    timeline = brief["timeline"]
    return {"duration_seconds": duration,
            "overview": {"bpm": overview.get("bpm_candidate"), "sound_candidates": overview.get("main_sound_candidates", []),
                         "mood_candidates": overview.get("mood_candidates", [])},
            "sections": [{"label": item.get("section_candidate"), "start_seconds": item["start"],
                          "end_seconds": item["end"], "energy": item.get("energy"),
                          "energy_trend": item.get("energy_trend")} for item in timeline[:24]],
            "beat_count": len(cut_points["beats"]), "onset_count": len(cut_points["onsets"])}


def _stop_tree(process):
    try:
        children = psutil.Process(process.pid).children(recursive=True)
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
    except psutil.NoSuchProcess:
        pass
    finally:
        process.kill()
        process.wait()


def run_whole_analysis(audio, directory):
    configured = os.getenv("CREATOROS_BGM_UNDERSTANDING_ROOT")
    skill = Path(configured).expanduser().resolve() if configured else Path.home() / ".codex/skills/bgm-understanding"
    script = skill / "scripts/understand_bgm.py"
    if not script.is_file():
        raise ValueError("缺少 bgm-understanding 分析器。请安装或配置 CREATOROS_BGM_UNDERSTANDING_ROOT。")
    if audio["duration_seconds"] > MAX_SECONDS + .05:
        raise ValueError("全曲分析当前支持最长 600 秒的音频。这首音乐仍可播放和选择。")
    configured_python = os.getenv("CREATOROS_BGM_PYTHON")
    python = [configured_python] if configured_python else (["py", "-3.11"] if os.name == "nt" else [sys.executable])
    output = directory / "output"
    command = [*python, str(script), "--audio", str(audio["path"]), "--output-dir", str(output), "--stage-timeout", "600"]
    try:
        with subprocess.Popen(command, cwd=directory, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}) as process:
            try:
                code = process.wait(timeout=ANALYSIS_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as error:
                _stop_tree(process)
                raise ValueError("全曲分析超时，可重试。") from error
    except OSError as error:
        raise ValueError("无法启动 bgm-understanding，请检查 Python 3.11 和本机分析环境。") from error
    if code:
        stage = next((name for name in ("signals", "features", "structure", "semantics")
                      if (output / f"{name}.log").exists() and not (output / "final-validation.json").exists()), None)
        if stage:
            raise ValueError("全曲分析未完成，请检查 bgm-understanding 的运行时和模型依赖后重试。")
        raise ValueError("bgm-understanding 未通过全曲分析校验，请核对素材、分析器和运行时后重试。")
    try:
        validation = json.loads(read_analysis(output / "final-validation.json"))
        receipt = json.loads(read_analysis(output / "raw/source-receipt.json"))
        raw = read_analysis(output / "bgm-understanding.json")
        brief = json.loads(raw)
        summary = full_summary(brief, audio["sha256"])
        if (validation.get("valid") is not True or receipt.get("requestedSelection") != "whole_file"
                or receipt.get("inputSha256") != audio["sha256"] or receipt.get("sourceStartSeconds") != 0
                or abs(receipt["sourceEndSeconds"] - summary["duration_seconds"]) > .05):
            raise ValueError("分析结果未通过全曲来源校验。")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError("bgm-understanding 没有产生可复用的全曲分析，请核对本机分析环境后重试。") from error
    return raw, {"schema": SCHEMA, "analyzer_version": analyzer_version(skill), "summary": summary}
