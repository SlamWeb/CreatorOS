"""Measure onset candidates in a bounded local audio range; never fill a beat grid."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
import math
import re
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from scipy import ndimage, signal
from scipy.io import wavfile

SR, HOP = 22050, 110


def source_duration(source: Path, ffmpeg: str) -> float:
    """Read WAV sample duration exactly; otherwise use ffmpeg container metadata."""
    try:
        with wave.open(str(source), "rb") as handle:
            return handle.getnframes() / handle.getframerate()
    except (wave.Error, EOFError):
        pass
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(source)],
                           capture_output=True, text=True, encoding="utf8", errors="replace")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr)
    if not match:
        raise ValueError("Cannot read source duration; supply a seekable local audio file with duration metadata.")
    hours, minutes, seconds = map(float, match.groups())
    return hours * 3600 + minutes * 60 + seconds


def decode(source: Path, ffmpeg: str, start: float, duration: float) -> np.ndarray:
    # Output seeking keeps the requested file-relative time convention for compressed audio.
    result = subprocess.run([ffmpeg, "-v", "error", "-i", str(source), "-ss", str(start),
                             "-t", str(duration), "-vn", "-ac", "1", "-ar", str(SR),
                             "-f", "f32le", "pipe:1"], capture_output=True)
    if result.returncode:
        raise ValueError("ffmpeg decode failed: " + result.stderr.decode("utf8", errors="replace")[-2000:])
    audio = np.frombuffer(result.stdout, dtype="<f4")
    if len(audio) < 1024:
        raise ValueError("Selected range has fewer than 1024 decoded samples; select a longer audible range.")
    if not np.all(np.isfinite(audio)):
        raise ValueError("Decoded audio contains non-finite samples.")
    return audio


def onset_envelope(audio: np.ndarray, nfft: int) -> tuple[np.ndarray, np.ndarray]:
    frequencies, times, spectrum = signal.stft(audio, fs=SR, nperseg=nfft,
        noverlap=nfft-HOP, boundary="zeros", padded=True)
    magnitude = np.abs(spectrum[(frequencies >= 90) & (frequencies <= 8000)])
    log_spectrum = np.log1p(magnitude / (magnitude.mean(axis=1, keepdims=True) + 1e-7))
    flux = np.maximum(np.diff(log_spectrum, axis=1, prepend=log_spectrum[:, :1]), 0).mean(axis=0)
    flux = ndimage.gaussian_filter1d(flux, .5)
    novelty = np.maximum(flux - ndimage.median_filter(flux, size=41, mode="nearest"), 0)
    novelty /= max(float(novelty.max()), 1e-12)
    return times, novelty


def detect(audio: np.ndarray, offset: float, min_gap: float, height: float,
           prominence: float, event_type: str) -> tuple[list[dict], np.ndarray, np.ndarray]:
    times, short = onset_envelope(audio, 512)
    _, long = onset_envelope(audio, 1024)
    novelty = .65 * short + .35 * long
    novelty /= max(float(novelty.max()), 1e-12)
    peaks, properties = signal.find_peaks(novelty, distance=max(1, round(min_gap * SR/HOP)),
                                           height=height, prominence=prominence)
    highpassed = signal.sosfilt(signal.butter(2, 80, fs=SR, btype="highpass", output="sos"), audio)
    power = ndimage.gaussian_filter1d(highpassed * highpassed, .002 * SR)
    shift = round(.005 * SR)
    delayed = np.r_[np.repeat(power[0], shift), power[:-shift]]
    rising = ndimage.gaussian_filter1d(np.maximum(power-delayed, 0), .001 * SR)
    candidates = []
    for peak, peak_prominence in zip(peaks, properties["prominences"]):
        spectral_time = float(times[peak])
        # Padded trailing frames can resemble an attack when a range ends mid-note.
        if spectral_time > len(audio)/SR - .04:
            continue
        lo = max(0, round((spectral_time-.012)*SR))
        hi = min(len(audio), round((spectral_time+.030)*SR))
        if hi <= lo:
            continue
        anchor = lo + int(np.argmax(rising[lo:hi]))
        local = power[max(0, anchor-round(.025*SR)):min(len(audio), anchor+round(.025*SR))]
        support = float(rising[anchor] / max(float(local.max()), 1e-12))
        agrees = short[peak] >= .05 and long[peak] >= .04
        strength = float(np.clip(novelty[peak], 0, 1))
        confidence = ("high" if strength >= .16 and support >= .035 and agrees else
                      "medium" if support >= .02 and strength >= .075 else "low")
        candidates.append({"timeSeconds": round(offset + anchor/SR, 6),
            "strength": round(strength, 6), "confidence": confidence, "type": event_type,
            "accepted": confidence != "low",
            "diagnostics": {"spectralPeakSeconds": round(offset+spectral_time, 6),
                "waveformAnchorDeltaMs": round((anchor/SR-spectral_time)*1000, 3),
                "prominence": round(float(peak_prominence), 6),
                "shortWindowStrength": round(float(short[peak]), 6),
                "longWindowStrength": round(float(long[peak]), 6),
                "waveformRiseSupport": round(support, 6)}})
    candidates.sort(key=lambda item: item["timeSeconds"])
    deduplicated = []
    # Keep the original 105ms re-anchor guard at default 110ms spacing; scale with the CLI gap.
    reanchor_gap = max(0, min_gap-HOP/SR)
    for event in candidates:
        if deduplicated and event["timeSeconds"]-deduplicated[-1]["timeSeconds"] < reanchor_gap:
            if event["strength"] > deduplicated[-1]["strength"]:
                deduplicated[-1] = event
        else:
            deduplicated.append(event)
    for index, event in enumerate(deduplicated, 1):
        event["id"] = f"event-{index:04d}"
    return deduplicated, times, novelty


def plot_review(path: Path, audio: np.ndarray, times: np.ndarray, novelty: np.ndarray,
                events: list[dict], offset: float) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(2, 1, figsize=(16, 7), sharex=True, constrained_layout=True)
    decimation = max(1, SR//1000)
    axes[0].plot(offset+np.arange(0, len(audio), decimation)/SR, audio[::decimation], lw=.4)
    axes[0].set_ylabel("Mono waveform")
    axes[1].plot(offset+times, novelty, lw=.8)
    axes[1].set_ylabel("Spectral novelty")
    axes[1].set_xlabel("Seconds from input audio start")
    for axis in axes:
        for event in events:
            axis.axvline(event["timeSeconds"], color="#299c77" if event["accepted"] else "#bd8a30",
                         lw=.7, alpha=.65)
        axis.set_xlim(offset, offset+len(audio)/SR)
        axis.grid(alpha=.15)
    figure.suptitle("Onset candidates: green auto-accepted; amber low confidence; listening unverified")
    figure.savefig(path, dpi=130)
    plt.close(figure)


def write_click_track(path: Path, audio: np.ndarray, events: list[dict], offset: float) -> None:
    mixed = np.array(audio, dtype=np.float64) * .7
    click_time = np.arange(round(.020*SR))/SR
    for event in events:
        anchor = round((event["timeSeconds"]-offset)*SR)
        length = min(len(click_time), len(mixed)-anchor)
        if anchor < 0 or length <= 0:
            continue
        # Low candidates remain audible at a lower pitch for review.
        frequency = 1800 if event["accepted"] else 900
        click = .25*np.sin(2*np.pi*frequency*click_time[:length])*np.exp(-click_time[:length]*180)
        mixed[anchor:anchor+length] += click
    mixed /= max(1, float(np.max(np.abs(mixed))))
    wavfile.write(path, SR, np.round(mixed*32767).astype(np.int16))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", type=float, default=0, help="Input-file seconds; retained in output timestamps")
    parser.add_argument("--duration", type=float, help="Analyze only this many seconds from --start")
    parser.add_argument("--max-analysis-seconds", type=float, default=120,
                        help="Memory guard; choose a range or explicitly raise this limit")
    parser.add_argument("--min-gap", type=float, default=.11, help="Minimum spectral peak gap in seconds")
    parser.add_argument("--height", type=float, default=.055, help="Normalized novelty peak threshold [0,1]")
    parser.add_argument("--prominence", type=float, default=.045, help="Normalized peak prominence [0,1]")
    parser.add_argument("--event-type", choices=["onset", "piano_attack"], default="onset",
                        help="piano_attack records the caller's source assumption, not an instrument classification")
    parser.add_argument("--plot", action="store_true", help="Write waveform/novelty review PNG")
    parser.add_argument("--click-track", action="store_true", help="Write selected-range WAV with candidate clicks")
    parser.add_argument("--write-wav", action="store_true", help="Write unmodified decoded selected-range mono WAV")
    args = parser.parse_args()
    numeric = [args.start, args.max_analysis_seconds, args.min_gap, args.height, args.prominence]
    if args.duration is not None:
        numeric.append(args.duration)
    if not all(math.isfinite(value) for value in numeric):
        parser.error("All numeric arguments must be finite.")
    if args.start < 0 or args.min_gap <= 0 or args.max_analysis_seconds <= 0:
        parser.error("--start must be >=0; --min-gap and --max-analysis-seconds must be >0.")
    if not 0 <= args.height <= 1 or not 0 <= args.prominence <= 1:
        parser.error("--height and --prominence must lie in [0,1].")
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be >0.")
    source = args.audio.expanduser().resolve()
    if not source.is_file():
        parser.error(f"Input audio does not exist: {source}")
    output = args.output_dir.expanduser().resolve()
    artifact_names = ["events.json"] + (["audio-novelty.png"] if args.plot else []) + (["click-review.wav"] if args.click_track else []) + (["analysis-audio.wav"] if args.write_wav else [])
    if any((output/name).exists() for name in artifact_names):
        parser.error("Output artifacts already exist; use a fresh output directory to preserve reviewed edits.")
    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        duration = source_duration(source, ffmpeg)
        if args.start >= duration:
            raise ValueError("--start must precede the end of the input audio.")
        selected_duration = min(args.duration if args.duration is not None else duration-args.start,
                                duration-args.start)
        if selected_duration > args.max_analysis_seconds + 1e-6:
            raise ValueError(f"Selected range is {selected_duration:.3f}s, exceeding the {args.max_analysis_seconds:g}s memory guard; choose --start/--duration or explicitly raise --max-analysis-seconds.")
        audio = decode(source, ffmpeg, args.start, selected_duration)
        events, times, novelty = detect(audio, args.start, args.min_gap, args.height, args.prominence, args.event_type)
        output.mkdir(parents=True, exist_ok=True)
        artifacts = {}
        if args.plot:
            plot_review(output/"audio-novelty.png", audio, times, novelty, events, args.start)
            artifacts["plot"] = str(output/"audio-novelty.png")
        if args.click_track:
            write_click_track(output/"click-review.wav", audio, events, args.start)
            artifacts["clickTrack"] = str(output/"click-review.wav")
        if args.write_wav:
            wavfile.write(output/"analysis-audio.wav", SR, audio)
            artifacts["analysisAudio"] = str(output/"analysis-audio.wav")
        report = {"schemaVersion": 1, "sourceAudio": str(source), "durationSeconds": duration,
            "analysisOffset": args.start, "analysisDurationSeconds": len(audio)/SR,
            "analysisRangeSeconds": [args.start, args.start+len(audio)/SR],
            "timeBase": "Seconds from the beginning of sourceAudio; analysisOffset already included in every event.",
            "eventTypeAssumption": args.event_type,
            "instrumentClassificationPerformed": False,
            "events": events, "candidateCount": len(events),
            "acceptedCount": sum(event["accepted"] for event in events),
            "method": {"sampleRate": SR, "hopSamples": HOP, "hopSeconds": HOP/SR,
                "windowsSamples": [512, 1024], "frequencyBandHz": [90, 8000],
                "spectralDetector": "Positive log spectral flux after per-frequency mean normalization; Gaussian smoothing 0.5 hops; subtract 41-hop median; normalized 0.65 short + 0.35 long windows.",
                "waveformAnchor": "80Hz highpass; Gaussian power sigma=2ms; positive 5ms power difference smoothed sigma=1ms; maximum rise from -12ms to +30ms around the spectral peak.",
                "minGapSeconds": args.min_gap, "height": args.height, "prominence": args.prominence,
                "reanchorDedupGapSeconds": max(0, args.min_gap-HOP/SR),
                "trailingGuardSeconds": .04, "beatGridAdded": False,
                "confidence": "Heuristic signal-support categories, not calibrated probabilities: high requires novelty>=.16, rise support>=.035, and both scales; medium requires novelty>=.075 and rise support>=.02; otherwise low.",
                "acceptance": "high/medium auto-accepted provisionally; low retained with accepted=false; user can edit, delete or add events.",
                "timingPrecision": "4.99ms spectral hop with sample-coordinate waveform anchor; perceptual uncertainty can be about +/-10-20ms or greater in mixtures. Sample coordinates do not establish sample-accurate perceptual alignment."},
            "humanListeningVerified": False, "humanReviewStatus": "unreviewed_candidates",
            "limitations": ["No instrument isolation or classification; event type is a caller assumption.",
                "Mixture percussion, vocals, codec artifacts, overlapping notes and reverberation can add or hide events.",
                "Range boundaries and the last 40ms require listening review; cropped sustained sound can resemble a new onset.",
                "Strength is normalized within this analysis range; changing the range can change scores and detections.",
                "Candidate density does not prove BPM, downbeats, acceleration or completed synchronization."],
            "artifacts": artifacts}
        (output/"events.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
        print(json.dumps({"events": str(output/"events.json"), "durationSeconds": duration,
            "analysisOffset": args.start, "analysisDurationSeconds": len(audio)/SR,
            "candidateCount": len(events), "acceptedCount": report["acceptedCount"],
            "lowConfidenceCount": sum(event["confidence"] == "low" for event in events),
            "humanListeningVerified": False}, ensure_ascii=False, indent=2))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Audio analysis failed: {error}\n")


if __name__ == "__main__":
    main()
