"""Validate the origin and time range of a saved BGM analysis."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path


def validate_analysis(audio: Path, receipt: dict, events: dict, *, audio_duration: float) -> None:
    """Check analysis matches audio and local timestamps fit the analysed clip."""
    audio = Path(audio).resolve(strict=True)
    digest = hashlib.sha256()
    with audio.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != receipt.get("inputSha256"):
        raise ValueError("BGM 分析与音频文件不匹配。")
    start = float(receipt["sourceStartSeconds"])
    end = float(receipt["sourceEndSeconds"])
    if not all(math.isfinite(x) for x in (start, end, audio_duration)) or not 0 <= start < end <= audio_duration + .05:
        raise ValueError("BGM 分析片段超出音频范围。")
    offset = float(events.get("analysisOffset", 0))
    duration = float(events["analysisDurationSeconds"])
    if not math.isfinite(offset) or abs(offset) > .001 or not math.isfinite(duration) or abs(duration - (end - start)) > .1:
        raise ValueError("BGM 起音结果与分析片段不一致。")
    for event in events["events"]:
        local = float(event["timeSeconds"])
        if not math.isfinite(local) or not 0 <= local <= duration:
            raise ValueError("BGM 起音时间超出分析片段。")
