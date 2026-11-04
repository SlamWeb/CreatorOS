"""Merge three measured audio reports into a bounded, source-attributed agent report.

Standard library only. The three inputs must refer to the same existing WAV.
No missing analysis is substituted and no new beat/onset timestamps are invented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import struct
from pathlib import Path


def number(value, name, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return float(value)


def read_report(path):
    report = json.loads(path.read_text(encoding="utf-8-sig"),
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Nonfinite JSON: {value}")))
    if not isinstance(report, dict) or report.get("schemaVersion") != 1:
        raise ValueError(f"{path}: expected schemaVersion 1 object")
    if report.get("error") or report.get("status") in ("error", "failed"):
        raise ValueError(f"{path}: analysis reports failure")
    return report


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wav_info(path):
    """Read RIFF duration without decoding, including IEEE-float and extensible WAV."""
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError(f"Source must be a RIFF WAV (PCM or float): {path}")
        fmt, data_size, data_chunks = None, 0, []
        file_size = path.stat().st_size
        while handle.tell() + 8 <= file_size:
            chunk_id, size = struct.unpack("<4sI", handle.read(8))
            if handle.tell() + size > file_size:
                raise ValueError(f"Truncated WAV chunk: {path}")
            if chunk_id == b"fmt ":
                raw = handle.read(size)
                if len(raw) < 16:
                    raise ValueError("WAV fmt chunk too short")
                fmt = struct.unpack("<HHIIHH", raw[:16])
                if fmt[0] == 65534:
                    if len(raw) < 40:
                        raise ValueError("Extensible WAV fmt chunk too short")
                    fmt = (struct.unpack("<H", raw[24:26])[0],) + fmt[1:]
            else:
                if chunk_id == b"data":
                    data_size += size
                    data_chunks.append((handle.tell(), size))
                handle.seek(size, 1)
            if size % 2:
                handle.seek(1, 1)
        if fmt is None or not data_size:
            raise ValueError("WAV must contain fmt and nonempty data chunks")
        kind, channels, rate, byte_rate, align, bits = fmt
        if kind not in (1, 3) or min(channels, rate, align, bits) <= 0:
            raise ValueError("Unsupported WAV encoding or invalid fmt fields")
        if byte_rate != rate * align or data_size % align:
            raise ValueError("Inconsistent WAV sample dimensions")
        return {"sampleRate": rate, "channels": channels, "durationSeconds": data_size / (rate * align),
                "encoding": kind, "bits": bits, "blockAlign": align, "dataChunks": data_chunks}


def waveform_level(path, info, start, end):
    """Measure source samples in requested range; prevents normalized scores hiding silence."""
    kind, bits = info["encoding"], info["bits"]
    codes = {(1, 8): "B", (1, 16): "h", (1, 32): "i", (3, 32): "f", (3, 64): "d"}
    if (kind, bits) not in codes and (kind, bits) != (1, 24):
        raise ValueError(f"Unsupported WAV sample encoding for silence check: {kind}/{bits}")
    bytes_per_sample, align = bits // 8, info["blockAlign"]
    if align != bytes_per_sample * info["channels"]:
        raise ValueError("Unsupported padded WAV frame layout")
    first_frame = max(0, math.floor(start * info["sampleRate"]))
    last_frame = min(round(info["durationSeconds"] * info["sampleRate"]), math.ceil(end * info["sampleRate"]))
    square_sum, peak, count, frame_offset = 0., 0., 0, 0
    with path.open("rb") as handle:
        for chunk_start, chunk_size in info["dataChunks"]:
            chunk_frames = chunk_size // align
            left, right = max(0, first_frame-frame_offset), min(chunk_frames, last_frame-frame_offset)
            if right > left:
                handle.seek(chunk_start + left * align)
                remaining = (right-left) * align
                while remaining:
                    size = min(remaining, max(align, (1024*1024 // align)*align))
                    block = handle.read(size)
                    if len(block) != size:
                        raise ValueError("Source WAV changed or truncated during level measurement")
                    if (kind, bits) == (1, 24):
                        values = (int.from_bytes(block[i:i+3], "little", signed=True) / 8388608 for i in range(0, len(block), 3))
                    else:
                        decoded = struct.iter_unpack("<"+codes[(kind, bits)], block)
                        scale = 1 if kind == 3 else 2 ** (bits-1)
                        bias = 128 if (kind, bits) == (1, 8) else 0
                        values = ((item[0]-bias)/scale for item in decoded)
                    for value in values:
                        if not math.isfinite(value):
                            raise ValueError("Source WAV contains nonfinite samples")
                        square_sum += value * value
                        peak = max(peak, abs(value))
                        count += 1
                    remaining -= size
            frame_offset += chunk_frames
    if not count:
        raise ValueError("No source samples in selected window")
    rms = math.sqrt(square_sum/count)
    return {"source": "direct source WAV samples in selected window, all channels",
            "rmsFullScale": round(rms, 9), "peakFullScale": round(peak, 9),
            "rmsDbFS": round(20*math.log10(rms), 3) if rms else None,
            "digitalSilence": peak == 0, "nearSilent": rms <= 1e-5 and peak <= 1e-4,
            "nearSilenceThresholds": {"rmsAtMost": 1e-5, "peakAtMost": 1e-4},
            "thresholdNote": "Heuristic near-silence threshold, not a calibrated audibility judgment; null dBFS for zero RMS."}


def timestamps(values, name, duration):
    if not isinstance(values, list):
        raise ValueError(f"{name} must be an array")
    result = [number(value, name, 0) for value in values]
    if any(value > duration + .002 for value in result):
        raise ValueError(f"{name} contains timestamps outside source duration")
    if any(right <= left for left, right in zip(result, result[1:])):
        raise ValueError(f"{name} must be strictly increasing")
    return result


def sonara_timestamps(values, name, duration, padding, tail_artifacts):
    """Sonara STFT padding can place a final frame just after file duration."""
    result = timestamps(values, name, duration + padding)
    outside = [value for value in result if value > duration]
    if len(outside) > 2 or any(value - duration > padding + 1e-6 for value in outside):
        raise ValueError(f"{name}: excessive out-of-range timestamps; not a small trailing-frame artifact")
    if outside:
        tail_artifacts.append({"field": name, "removedCount": len(outside),
                               "removedSeconds": outside,
                               "maximumExcessSeconds": rounded(max(outside)-duration)})
    return [value for value in result if value <= duration]


def rounded(value):
    return round(float(value), 4)


def mean(values):
    return statistics.fmean(values) if values else None


def density(events, start, end):
    return sum(start <= event["timeSeconds"] < end for event in events) / (end - start)


def energy_mean(curve, hop, start, end):
    # Samples are indexed by the Sonara hop; no interpolation or false frame precision.
    return mean([value for i, value in enumerate(curve) if start <= i * hop < end])


def tempo_summary(sonara_bpm, beats):
    intervals = [right - left for left, right in zip(beats, beats[1:])]
    interval = statistics.median(intervals) if intervals else None
    predicted = 60 / interval if interval else None
    candidate = sonara_bpm if sonara_bpm > 0 else None
    ratio = candidate / predicted if candidate and predicted else None
    relation = "unavailable"
    if ratio is not None:
        relation = "inconsistent"
        for target, label in [(1, "approximately_agree"), (2, "sonara_approximately_double"),
                              (.5, "sonara_approximately_half")]:
            if abs(ratio / target - 1) <= .08:
                relation = label
                break
    return {"sonaraBpmCandidate": rounded(candidate) if candidate else None,
            "beatThisMedianIntervalBpm": rounded(predicted) if predicted else None,
            "sonaraToBeatThisRatio": rounded(ratio) if ratio else None,
            "relation": relation, "relativeTolerance": .08,
            "beatThisIntervalCount": len(intervals),
            "beatThisIntervalMadSeconds": rounded(statistics.median(abs(x-interval) for x in intervals)) if intervals else None,
            "interpretation": "Independent estimates; ratio near 2 or 0.5 is a half/double-tempo ambiguity, not evidence of playback speed or tempo change. No canonical BPM chosen."}


def merge(sonara, beats, onsets, paths, window_start=None, window_duration=None,
          max_onsets=24, max_windows=24, max_changes=8, max_segments=32):
    sa = sonara["audio"]
    if sa.get("timeOrigin") != "input file start" or beats.get("timeOrigin") != "input WAV start":
        raise ValueError("Unrecognized Sonara/Beat This time origin")
    if onsets.get("timeBase") != "Seconds from the beginning of sourceAudio; analysisOffset already included in every event.":
        raise ValueError("Unrecognized onset timeBase; event offsets must already be included")
    sources = [Path(sa["source"]).expanduser().resolve(),
               Path(beats["sourceAudio"]).expanduser().resolve(),
               Path(onsets["sourceAudio"]).expanduser().resolve()]
    if any(not path.is_file() for path in sources):
        raise ValueError("Every declared sourceAudio must exist for hash verification")
    # Requiring the same resolved file also gives the unhashed onset report an explicit identity check.
    if any(path != sources[0] for path in sources[1:]):
        raise ValueError("Source WAV paths differ; rerun all analyzers against the same WAV")
    actual_hash = sha256(sources[0])
    if sa.get("sha256", "").lower() != actual_hash or beats.get("audioSha256", "").lower() != actual_hash:
        raise ValueError("Sonara/Beat This audio hashes disagree with current source WAV")
    if onsets.get("audioSha256") and onsets["audioSha256"].lower() != actual_hash:
        raise ValueError("Onset audio hash disagrees with source WAV")
    info = wav_info(sources[0])
    duration = info["durationSeconds"]
    for name, value in [("sonara duration", sa["durationSeconds"]),
                        ("beat duration", beats["durationSeconds"]), ("onset duration", onsets["durationSeconds"])]:
        if abs(number(value, name, 0) - duration) > .01:
            raise ValueError(f"{name} differs from source WAV by more than 10ms")
    if beats.get("sampleRate") != info["sampleRate"]:
        raise ValueError("Beat This sampleRate differs from source WAV")
    offset = number(onsets["analysisOffset"], "analysisOffset", 0)
    analyzed = number(onsets["analysisDurationSeconds"], "analysisDurationSeconds", 0)
    analysis_range = onsets["analysisRangeSeconds"]
    if len(analysis_range) != 2 or abs(number(analysis_range[0], "range start") - offset) > .002 or abs(number(analysis_range[1], "range end") - (offset + analyzed)) > .002:
        raise ValueError("Onset analysis range/offset/duration disagree")
    if analyzed <= 0 or offset + analyzed > duration + .002:
        raise ValueError("Onset analysis range outside source WAV")
    start = offset if window_start is None else number(window_start, "window start", 0)
    end = start + (min(duration, offset+analyzed)-start if window_duration is None else number(window_duration, "window duration", 0))
    if start < offset - .001 or end > min(duration, offset+analyzed) + .001 or end <= start:
        raise ValueError("Requested window must be nonempty and covered by onset analysis range")
    level = waveform_level(sources[0], info, start, end)
    beat_times = timestamps(beats["beatSeconds"], "beatSeconds", duration)
    downbeats = timestamps(beats["downbeatSeconds"], "downbeatSeconds", duration)
    # A narrowly bounded trailing STFT artifact is recorded, never clamped onto a fake end beat.
    provenance = sonara.get("engine", {}).get("provenance", {})
    frame_hop = number(provenance.get("hop_length", 0), "Sonara frame hop", 0)
    frame_rate = number(provenance.get("sample_rate", 1), "Sonara sample rate", 1)
    padding = min(.1, 2 * frame_hop / frame_rate)
    tail_artifacts = []
    sonara_beats = sonara_timestamps(sonara["rhythm"]["beatsSeconds"], "rhythm.beatsSeconds", duration, padding, tail_artifacts)
    sonara_timestamps(sonara["rhythm"]["downbeatsSeconds"], "rhythm.downbeatsSeconds", duration, padding, tail_artifacts)
    if "onsets" in sonara:
        sonara_timestamps(sonara["onsets"]["candidateSeconds"], "onsets.candidateSeconds", duration, padding, tail_artifacts)
    curve = [number(value, "energyCurve", 0) for value in sonara["dynamics"]["energyCurve"]]
    hop = number(sonara["dynamics"]["energyCurveHopSeconds"], "energy hop", 0)
    if not curve or hop <= 0 or abs(len(curve) * hop - duration) > max(1.1, 2 * hop):
        raise ValueError("Energy curve empty or hop/length inconsistent with duration")
    if any(value > 1.001 for value in curve):
        raise ValueError("Expected Sonara normalized [0,1] energy curve")
    events = onsets["events"]
    if not isinstance(events, list):
        raise ValueError("Onset events must be an array")
    event_times = timestamps([event["timeSeconds"] for event in events], "onset events", duration)
    for event, time in zip(events, event_times):
        if not offset <= time <= offset + analyzed + .002:
            raise ValueError("Onset event lies outside declared analysis range")
        strength = number(event["strength"], "onset strength", 0)
        if strength > 1 or type(event.get("accepted")) is not bool or event.get("confidence") not in ("high", "medium", "low"):
            raise ValueError("Invalid onset strength/acceptance/confidence")
    selected = [event for event in events if start <= event["timeSeconds"] < end]
    accepted = [event for event in selected if event["accepted"]]
    strong = sorted(accepted, key=lambda event: (-event["strength"], event["timeSeconds"]))[:max_onsets]
    strongest = [{key: event[key] for key in ("id", "timeSeconds", "strength", "confidence", "type") if key in event}
                 for event in sorted(strong, key=lambda event: event["timeSeconds"])]
    # Fixed summary windows are measurement bins, never suggested cut points.
    length = end - start
    bin_size = max(1., length / max_windows)
    count = min(max_windows, math.ceil(length / bin_size))
    windows = []
    for i in range(count):
        left, right = start + i * bin_size, min(end, start + (i+1)*bin_size)
        value = None if level["nearSilent"] else energy_mean(curve, hop, left, right)
        windows.append({"rangeSeconds": [rounded(left), rounded(right)],
                        "meanEnergy": rounded(value) if value is not None else None,
                        "acceptedOnsetCount": sum(left <= event["timeSeconds"] < right for event in accepted),
                        "onsetsPerSecond": rounded(density(accepted, left, right))})
    measured_energy = [value for i, value in enumerate(curve) if start <= i * hop < end]
    energy_stats = {"min": rounded(min(measured_energy)), "max": rounded(max(measured_energy)),
                    "mean": rounded(mean(measured_energy))} if measured_energy else None
    if level["nearSilent"]:
        energy_stats = None
        strongest = []
    # Two-sided comparisons use actual curve sample positions. Nearby candidates are suppressed.
    side = min(2., max(.75, length / 8))
    raw_changes = []
    for i in range(1, len(curve)):
        if level["nearSilent"]:
            break
        time = i * hop
        if time - side < start or time + side > end:
            continue
        before = energy_mean(curve, hop, time-side, time)
        after = energy_mean(curve, hop, time, time+side)
        if before is None or after is None:
            continue
        delta = after - before
        relative = abs(delta) / max(abs(before), abs(after), .02)
        d_before, d_after = density(accepted, time-side, time), density(accepted, time, time+side)
        d_delta = d_after - d_before
        e_flag = abs(delta) >= .06 and relative >= .25
        d_flag = abs(d_delta) >= 1.5 and abs(d_delta) / max(d_before, d_after, .5) >= .5
        if not (e_flag or d_flag):
            continue
        score = max(abs(delta)/.06 if e_flag else 0, abs(d_delta)/1.5 if d_flag else 0)
        raw_changes.append({"timeSeconds": rounded(time), "score": rounded(score),
                            "signals": (["energy_increase" if delta > 0 else "energy_decrease"] if e_flag else []) +
                                       (["onset_density_increase" if d_delta > 0 else "onset_density_decrease"] if d_flag else []),
                            "comparisonRangeSeconds": [rounded(time-side), rounded(time+side)],
                            "energyBeforeAfter": [rounded(before), rounded(after)],
                            "onsetsPerSecondBeforeAfter": [rounded(d_before), rounded(d_after)]})
    changes = []
    min_spacing = max(1., side)
    for change in sorted(raw_changes, key=lambda item: (-item["score"], item["timeSeconds"])):
        if all(abs(change["timeSeconds"] - item["timeSeconds"]) >= min_spacing for item in changes):
            changes.append(change)
            if len(changes) >= max_changes:
                break
    segments = []
    for segment in sonara["segments"]:
        left = number(segment["start_sec"], "segment start", 0)
        right = number(segment["end_sec"], "segment end", 0)
        value = number(segment["energy"], "segment energy", 0)
        if right <= left or right > duration + .01:
            raise ValueError("Invalid Sonara segment range")
        if right > start and left < end:
            segments.append({"rangeSeconds": [rounded(left), rounded(right)], "meanEnergy": rounded(value)})
    drift = None
    if energy_stats:
        first = energy_mean(curve, hop, start, start+length/3)
        last = energy_mean(curve, hop, end-length/3, end)
        if first is not None and last is not None:
            delta = last-first
            drift = {"firstThirdMean": rounded(first), "lastThirdMean": rounded(last),
                     "delta": rounded(delta), "heuristicDirection": "increase" if delta >= .06 else "decrease" if delta <= -.06 else "no_large_mean_shift"}
    source_records = {}
    for name, path in paths.items():
        source_records[name] = {"path": str(path.resolve()), "sha256": sha256(path)}
    source_records["sonara"]["engine"] = {key: value for key, value in sonara.get("engine", {}).items() if key in ("name", "version")}
    tempo = tempo_summary(number(sonara["rhythm"]["bpmCandidate"], "Sonara BPM", 0), beat_times)
    tempo["reportedSonaraBpmCandidate"] = rounded(sonara["rhythm"]["bpmCandidate"])
    if not sonara_beats or level["nearSilent"]:
        tempo["sonaraBpmCandidate"] = None
    if level["nearSilent"]:
        tempo.update(beatThisMedianIntervalBpm=None, sonaraToBeatThisRatio=None,
                     relation="unknown_near_silent_window", interpretation="Source waveform is near silent; analyzer tempo defaults/predictions are unsupported. Reported arrays preserved solely for traceability.")
    elif tempo["sonaraBpmCandidate"] is None:
        tempo.update(sonaraToBeatThisRatio=None, relation="sonara_unknown_no_beat_support")
    if level["nearSilent"]:
        segments = []
    return {
        "schemaVersion": 1, "type": "bgm_signal_summary",
        "audio": {"source": str(sources[0]), "sha256": actual_hash, "durationSeconds": rounded(duration),
                  "sampleRate": info["sampleRate"], "timeOrigin": "input WAV start", "analysisWindowSeconds": [rounded(start), rounded(end)]},
        "verification": {"sourceWavHashVerified": True, "threeReportsSameResolvedSource": True,
                         "durationToleranceSeconds": .01, "onsetHashPresentInInput": bool(onsets.get("audioSha256")),
                         "sonaraTailArtifacts": tail_artifacts,
                         "sonaraTailFilterRule": {"maximumExcessSeconds": rounded(padding), "maximumRemovedPerArray": 2,
                                                  "note": "Only final Sonara frames <=2 frame hops (capped at 100ms) beyond WAV duration are excluded and logged; larger/count>2 artifacts fail. Source JSON remains unchanged."},
                         "onsetIdentityCaveat": "Legacy onset report has no stored audio hash: source path, current WAV hash, range and duration verified; cannot prove bytes at original onset-analysis time." if not onsets.get("audioSha256") else None},
        "sources": source_records,
        "rhythm": {"source": "beats", "model": beats["model"], "modelSha256": beats.get("modelSha256"), "beatSeconds": beat_times,
                   "downbeatSeconds": downbeats, "scope": "entire source WAV; arrays preserved",
                   "tempo": tempo,
                   "note": "Model-predicted beats/downbeats, not instrument attacks. Sonara beat arrays remain in source report; no merging or grid filling."},
        "onsets": {"source": "onsets", "analyzedRangeSeconds": [rounded(offset), rounded(offset+analyzed)],
                   "windowCandidateCount": len(selected), "windowAcceptedCount": len(accepted),
                   "windowRejectedCount": sum(not event["accepted"] for event in selected),
                   "windowLowConfidenceCount": sum(event["confidence"] == "low" for event in selected),
                   "strongestAccepted": strongest, "strongestLimit": max_onsets,
                   "strongestOmittedCount": max(0, len(accepted)-len(strongest)),
                   "selection": "Auto-accepted detected events ranked by within-analysis normalized strength, then returned in time order; no invented events.",
                   "eventTypeAssumption": onsets["eventTypeAssumption"], "instrumentClassificationPerformed": False},
        "dynamics": {"energySource": "sonara.dynamics.energyCurve", "onsetDensitySource": "onsets.events accepted=true",
                     "waveformLevel": level,
                     "energyEvidenceState": "suppressed_near_silent_window" if level["nearSilent"] else "unreviewed_sonara_signal_score",
                     "silenceHandling": "Near-silent waveform overrides normalized energy scores, BPM and short-window changes; strongest onsets omitted. Model beat arrays remain untrusted predictions.",
                     "energyCurveHopSeconds": rounded(hop), "energyStatistics": energy_stats,
                     "energyTimingNote": "Sonara 0.3.6 samples start at i*hop and summarize about 0.99846s windows; adjacent scores overlap and cannot support millisecond change timing. Loudness curve is intentionally omitted.",
                     "trend": drift, "summaryWindows": windows,
                     "densityRangePerSecond": [min(window["onsetsPerSecond"] for window in windows), max(window["onsetsPerSecond"] for window in windows)],
                     "meanAcceptedOnsetsPerSecond": rounded(len(accepted)/length),
                     "windowMethod": "Consecutive measurement bins max(1 second, selected duration/maxWindows); final bin may be shorter. Mean energy samples indexed at i*Sonara hop. Counts use all accepted detected events, not only strongest."},
        "structure": {"longSectionsSource": "sonara.segments", "longSectionCandidates": segments[:max_segments],
                      "longSectionsOmittedCount": max(0, len(segments)-max_segments),
                      "longSectionNote": "Sonara 0.3.6 signal sections require at least 32 energy windows (about 16s) for internal boundaries, then impose 8s boundary spacing; not verse/chorus labels. Near-silent selected windows suppress these candidates.",
                      "shortWindowChangeCandidates": sorted(changes, key=lambda item: item["timeSeconds"]),
                      "shortWindowMethod": {"status": "heuristic candidates, not established sections or mandatory cuts",
                          "source": ["sonara.dynamics.energyCurve", "onsets.events accepted=true"],
                          "comparisonSideSeconds": rounded(side), "minimumCandidateSpacingSeconds": rounded(min_spacing),
                          "energyAbsoluteDeltaAtLeast": .06, "energyRelativeDeltaAtLeast": .25,
                          "densityAbsoluteDeltaAtLeastPerSecond": 1.5, "densityRelativeDeltaAtLeast": .5,
                          "relativeDenominatorFloors": {"energy": .02, "density": .5},
                          "logic": "energy absolute AND relative thresholds OR density absolute AND relative thresholds; ranked maximum normalized threshold exceedance, greedy spacing suppression",
                          "maximumCandidates": max_changes, "rawThresholdCrossings": len(raw_changes)}},
        "humanListeningVerified": False,
        "limitations": ["All detector results and summary thresholds are heuristic; strengths/confidence are not calibrated probabilities.",
                        "Energy is Sonara's normalized perceptual signal score, not loudness, instrument identity or semantic emotion. Energy timestamps use supplied hop; window support can smear changes.",
                        "Measurement bins are not detected sections or prescribed cuts. Empty change candidates can mean insufficient evidence or insufficient resolution.",
                        "Onset density measures detected accepted events, not true note count, BPM or acceleration. Sparse/silent audio can still yield beat-model predictions; beat prediction on a short crop may omit beats detected with full-context audio.",
                        "No instrument classification, source separation, listening validation or semantic section prediction. Raw reports remain separate."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sonara", "beats", "onsets", "output"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--window-start", type=float)
    parser.add_argument("--window-duration", type=float)
    parser.add_argument("--max-onsets", type=int, default=24)
    parser.add_argument("--max-windows", type=int, default=24)
    parser.add_argument("--max-changes", type=int, default=8)
    parser.add_argument("--max-segments", type=int, default=32)
    args = parser.parse_args()
    try:
        args.output = args.output.expanduser().resolve()
        if args.output.exists():
            raise FileExistsError("Refusing to overwrite output; choose a new filename")
        for name, ceiling in [("max_onsets", 64), ("max_windows", 48), ("max_changes", 16), ("max_segments", 64)]:
            if not 1 <= getattr(args, name) <= ceiling:
                raise ValueError(f"--{name.replace('_', '-')} must be 1..{ceiling}")
        paths = {name: getattr(args, name).expanduser().resolve() for name in ("sonara", "beats", "onsets")}
        reports = {name: read_report(path) for name, path in paths.items()}
        report = merge(**reports, paths=paths, window_start=args.window_start, window_duration=args.window_duration,
                       max_onsets=args.max_onsets, max_windows=args.max_windows,
                       max_changes=args.max_changes, max_segments=args.max_segments)
        # Serialize first: validation failures never leave a partial output artifact.
        serialized = json.dumps(report, ensure_ascii=False, separators=(",", ":"), allow_nan=False)+"\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
        print(json.dumps({"output": str(args.output.resolve()), "bytes": len(serialized.encode("utf-8")),
                          "durationSeconds": report["audio"]["durationSeconds"],
                          "bpmRelation": report["rhythm"]["tempo"]["relation"],
                          "strongestOnsets": len(report["onsets"]["strongestAccepted"]),
                          "changeCandidates": len(report["structure"]["shortWindowChangeCandidates"])}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, struct.error) as error:
        parser.exit(1, f"BGM merge failed: {error}\n")


if __name__ == "__main__":
    main()
