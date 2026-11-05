"""Evidence for B47 video inputs and rendered content; never grade unseen frames.

The collector binds isolated files to snapshot hashes, then samples video and
compares decoded audio.  Signal agreement is supporting evidence, not proof of
creative provenance or of content between samples.  No model is called.
"""
from __future__ import annotations

from io import BytesIO
from array import array
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from PIL import Image, ImageChops, ImageStat

from .visual_evidence import _read, UnavailableVisualEvidence
from creatoros.integrations.media_tools import media_tool

VERSION = "benchmark-video-evidence-v2"
MAX_DECODED_FRAMES = 150


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def _rows(snapshot, table):
    value = snapshot.get("database", {}).get(table)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise UnavailableVisualEvidence(f"Missing database table {table}")
    return value


def _read_snapshot(root, path, expected, files):
    root, path = Path(root).resolve(), Path(path)
    if not path.is_absolute():
        path = root / path
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as error:
        raise UnavailableVisualEvidence("Path is outside isolated world") from error
    if files.get(relative) != expected:
        raise UnavailableVisualEvidence("File is absent from snapshot or checksum differs")
    return relative, _read(root, relative, expected)


def _run(tool, args, timeout=35):
    result = subprocess.run([tool, "-v", "error", *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode:
        raise UnavailableVisualEvidence("Media decode failed or media stream is missing")
    return result.stdout


def _video_samples(tool, video, references):
    # Uniform samples are useful but never establish absence of unsampled frames.
    raw = _run(tool, ["-i", str(video), "-t", "3", "-vf", "fps=8,scale=64:64",
                      "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])
    frame_size = 64 * 64 * 3
    if not raw or len(raw) % frame_size:
        raise UnavailableVisualEvidence("No complete decoded video samples")
    refs = []
    for data in references:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 16_000_000:
                raise UnavailableVisualEvidence("Reference image dimensions exceed limit")
            image.load()
            refs.append(image.convert("RGB").resize((64, 64)))
    if ImageStat.Stat(ImageChops.difference(refs[0], refs[1])).mean == [0, 0, 0]:
        raise UnavailableVisualEvidence("Reference images indistinguishable")
    labels, distances = [], []
    for at in range(0, len(raw), frame_size):
        frame = Image.frombytes("RGB", (64, 64), raw[at:at + frame_size])
        errors = [sum(ImageStat.Stat(ImageChops.difference(frame, ref)).mean) / 3 for ref in refs]
        # Threshold is a conservative diagnostic for this fixed slideshow; a
        # miss may be transitions, resizing or deliberate visual adjustments.
        ranked = sorted(range(2), key=errors.__getitem__)
        label = ranked[0] if errors[ranked[0]] <= 18 and errors[ranked[1]] - errors[ranked[0]] >= 8 else None
        labels.append(label)
        distances.append([round(error, 2) for error in errors])
    clean = [x for x in labels if x is not None]
    if clean and 0 in clean and 1 in clean and clean == sorted(clean) and len(clean) == len(labels):
        assessment = "sampled_consistent"
    else:
        assessment = "inconclusive"
    return {"assessment": assessment, "fps": 8, "count": len(labels),
            "sample_labels": [None if value is None else value + 1 for value in labels],
            "mean_absolute_rgb_errors": distances,
            "limitation": "Uniform samples only; unmatched samples can be transitions or transforms; absence of other frames is not proven."}


def _video_all_frames(tool, video, references):
    """Examine all decoded frames when the entire stream fits the fixed bound.

    A 64px lossy comparison is diagnostic, even when no frames were skipped.
    It cannot rule out small changes, transformed originals, other streams,
    provenance substitution or differences in the content before downscaling.
    """
    raw = _run(tool, ["-xerror", "-i", str(video), "-map", "0:v:0", "-an",
                      "-fps_mode", "passthrough", "-frames:v", str(MAX_DECODED_FRAMES + 1),
                      "-vf", "scale=64:64", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])
    size = 64 * 64 * 3
    if not raw or len(raw) % size:
        raise UnavailableVisualEvidence("No complete decoded video frames")
    count = len(raw) // size
    if count > MAX_DECODED_FRAMES:
        return {"assessment": "unknown", "coverage": "limit_exceeded", "count_at_least": count,
                "frame_limit": MAX_DECODED_FRAMES,
                "limitation": "The stream exceeded the frame limit; no whole-stream claim is possible."}
    refs = []
    for data in references:
        with Image.open(BytesIO(data)) as picture:
            if picture.width * picture.height > 16_000_000:
                raise UnavailableVisualEvidence("Reference image dimensions exceed limit")
            picture.load()
            refs.append(picture.convert("RGB").resize((64, 64)))
    if len(refs) != 2 or ImageStat.Stat(ImageChops.difference(refs[0], refs[1])).mean == [0, 0, 0]:
        raise UnavailableVisualEvidence("Reference images indistinguishable")
    labels, distances = [], []
    for at in range(0, len(raw), size):
        frame = Image.frombytes("RGB", (64, 64), raw[at:at + size])
        errors = [sum(ImageStat.Stat(ImageChops.difference(frame, ref)).mean) / 3 for ref in refs]
        ranked = sorted(range(2), key=errors.__getitem__)
        label = ranked[0] if errors[ranked[0]] <= 18 and errors[ranked[1]] - errors[ranked[0]] >= 8 else None
        labels.append(label)
        distances.append([round(value, 2) for value in errors])
    clean = [label for label in labels if label is not None]
    consistent = len(clean) == count and set(clean) == {0, 1} and clean == sorted(clean)
    return {"assessment": "all_decoded_frames_consistent" if consistent else "inconclusive",
            "coverage": "all_decoded_frames", "count": count, "frame_limit": MAX_DECODED_FRAMES,
            "frame_labels": [None if label is None else label + 1 for label in labels],
            "mean_absolute_rgb_errors": distances,
            "limitation": ("The first video stream was decoded in full and compared after resizing to 64x64; "
                "transitions, edits and transforms can be inconclusive. Matching pixels do not prove source identity, "
                "small details, other streams or creative intent.")}


def _audio_samples(tool, video, reference):
    def pcm(path):
        raw = _run(tool, ["-i", str(path), "-t", "2.5", "-vn", "-ac", "1", "-ar", "8000",
                          "-f", "s16le", "pipe:1"])
        if len(raw) % 2:
            raise UnavailableVisualEvidence("Invalid audio samples")
        values = array("h")
        values.frombytes(raw)
        if sys.byteorder != "little":
            values.byteswap()
        return values
    rendered, original = pcm(video), pcm(reference)
    if len(rendered) < 4000 or len(original) < 4000:
        raise UnavailableVisualEvidence("Audio is too short to compare")
    peak = 0.0
    # Offset tolerance accommodates AAC delay; correlation does not uniquely
    # identify a source (e.g. two 440Hz tones), and cannot prove file provenance.
    for offset in range(-1600, 1601, 40):
        left, right = max(offset, 0), max(-offset, 0)
        length = min(len(original) - left, len(rendered) - right)
        if length < 4000:
            continue
        a, b = original[left:left+length], rendered[right:right+length]
        mean_a, mean_b = sum(a) / length, sum(b) / length
        sum_a = sum((x - mean_a) ** 2 for x in a)
        sum_b = sum((x - mean_b) ** 2 for x in b)
        norm = math.sqrt(sum_a * sum_b)
        if norm > 1e-6:
            product = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
            peak = max(peak, abs(product / norm))
    return {"assessment": "waveform_consistent" if peak >= 0.90 else "inconclusive",
            "max_normalized_correlation": round(peak, 4), "sample_rate": 8000,
            "limitation": "Acoustic similarity supports but cannot prove BGM file provenance; remixes and offsets may be inconclusive."}


def collect_video_evidence(root, evidence, *, ffmpeg=None):
    """Return defensible B47 evidence; all missing/unverifiable data stays unknown."""
    root = Path(root).resolve()
    result = {"version": VERSION, "status": "needs_review", "input_binding": {"status": "needs_review"},
              "frames": {"assessment": "unknown"}, "full_frames": {"assessment": "unknown"}, "audio": {"assessment": "unknown"}, "limitations": [
                  "Pixel samples are not a complete frame-by-frame proof.",
                  "Audio waveform similarity does not establish unique file provenance."]}
    try:
        before, after, variables = evidence["before"], evidence["after"], evidence["variables"]
        initial = evidence["background"]["initial_collections"][variables["collection_a"]]
        originals = initial["items"]
        if not isinstance(originals, list) or len(originals) != 2:
            raise UnavailableVisualEvidence("Original ordered two-image collection missing")
        collections = [r for r in _rows(before, "media_collections") if r.get("id") == variables["collection_a"]]
        if len(collections) != 1 or collections[0].get("creator_id") != variables["creator_a"]:
            raise UnavailableVisualEvidence("Original collection identity or owner mismatch")
        selected_runs = _json(collections[0].get("run_ids_json"))
        if not isinstance(selected_runs, list) or any(not isinstance(item, str) for item in selected_runs):
            raise UnavailableVisualEvidence("Original collection Run list missing")
        source_runs = _rows(before, "content_runs")
        source_revisions = _rows(before, "content_revisions")
        for item in originals:
            if not isinstance(item, dict) or item.get("run_id") not in selected_runs:
                raise UnavailableVisualEvidence("Collection item not bound to a source Run")
            runs = [r for r in source_runs if r.get("id") == item.get("run_id")]
            revisions = [r for r in source_revisions if r.get("id") == item.get("revision_id")
                         and r.get("content_run_id") == item.get("run_id")]
            if len(runs) != 1 or len(revisions) != 1 or runs[0].get("active_revision_number") != revisions[0].get("revision_number"):
                raise UnavailableVisualEvidence("Source Run or active revision unavailable")
            source_input = _json(revisions[0].get("production_input_json"))
            source_validation = _json(revisions[0].get("validation_json"))
            if not isinstance(source_input, dict) or source_input.get("creator_id") != variables["creator_a"] or not isinstance(source_validation, dict):
                raise UnavailableVisualEvidence("Source image owner or validation missing")
            entries = source_validation.get("images")
            entries = [row for row in entries if isinstance(row, dict) and row.get("order") == item.get("order")] if isinstance(entries, list) else []
            if len(entries) != 1 or entries[0].get("sha256") != item.get("checksum"):
                raise UnavailableVisualEvidence("Source image revision and collection checksum differ")
            _read_snapshot(root, Path(revisions[0]["artifact_directory"]) / entries[0]["path"], item["checksum"], before["files"])
        old = {r["id"] for r in source_runs}
        candidates = [r for r in _rows(after, "content_runs") if r.get("id") not in old and r.get("topic_id") == variables["topic_a"]]
        if len(candidates) != 1:
            raise UnavailableVisualEvidence("Unique new video Run unavailable")
        run = candidates[0]
        revisions = [r for r in _rows(after, "content_revisions") if r.get("content_run_id") == run["id"]
                     and r.get("revision_number") == run.get("active_revision_number")]
        if len(revisions) != 1:
            raise UnavailableVisualEvidence("Unique active revision unavailable")
        revision = revisions[0]
        frozen = _json(revision.get("production_input_json"))
        if not isinstance(frozen, dict) or frozen.get("creator_id") != variables["creator_a"] or frozen.get("topic_id") != variables["topic_a"] or frozen.get("task_id") != run.get("task_id"):
            raise UnavailableVisualEvidence("Run/Revision account, topic or task identity mismatch")
        spec = frozen.get("execution_spec")
        if not isinstance(spec, dict) or spec.get("kind") != "video":
            raise UnavailableVisualEvidence("Frozen video execution specification missing")
        skills, materials = spec.get("skills"), spec.get("materials")
        if not isinstance(skills, list) or not isinstance(materials, list):
            raise UnavailableVisualEvidence("Frozen Skill or material list missing")
        selected_skill = any(s.get("skill_id") == variables["video_skill"] for s in skills if isinstance(s, dict))
        audios = [x for x in materials if isinstance(x, dict) and x.get("kind") == "audio" and x.get("id") == variables["bgm_material"]]
        zips = [x for x in materials if isinstance(x, dict) and x.get("kind") == "images_zip"]
        if not selected_skill or len(audios) != 1 or len(zips) != 1:
            raise UnavailableVisualEvidence("Expected Skill, BGM or unique image ZIP missing")
        files = after["files"]
        _, audio_bytes = _read_snapshot(root, audios[0]["path"], audios[0]["sha256"], files)
        before_audio = before.get("files", {}).get("inputs/tone.wav")
        if not before_audio or hashlib.sha256(audio_bytes).hexdigest() != before_audio:
            raise UnavailableVisualEvidence("Frozen BGM differs from source input")
        _, zip_bytes = _read_snapshot(root, zips[0]["path"], zips[0]["sha256"], files)
        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or names.count("manifest.json") != 1:
                raise UnavailableVisualEvidence("Invalid frozen image ZIP")
            if len(names) != 3 or any(item.file_size > 20 * 1024 * 1024 for item in archive.infolist()):
                raise UnavailableVisualEvidence("Image ZIP has extra or oversized members")
            manifest = json.loads(archive.read("manifest.json"))
            items = manifest.get("items")
            if manifest.get("collection_id") != variables["collection_a"] or not isinstance(items, list) or len(items) != 2:
                raise UnavailableVisualEvidence("Frozen ZIP selection does not identify expected collection")
            wanted = [(x.get("run_id"), x.get("revision_id"), x.get("order"), x.get("checksum")) for x in originals]
            actual = [(x.get("run_id"), x.get("revision_id"), x.get("order"), x.get("checksum")) for x in items]
            if wanted != actual or any(not all(row) for row in actual):
                raise UnavailableVisualEvidence("Frozen ZIP image identity or order differs from collection")
            refs = []
            for item in items:
                name = item.get("filename")
                if not isinstance(name, str) or name not in names or name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
                    raise UnavailableVisualEvidence("Unsafe or missing image member")
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != item["checksum"]:
                    raise UnavailableVisualEvidence("Image ZIP member checksum differs")
                refs.append(data)
        video = _json(revision.get("validation_json"))
        video = video.get("video") if isinstance(video, dict) else None
        if not isinstance(video, dict) or not isinstance(video.get("path"), str):
            raise UnavailableVisualEvidence("Accepted video validation missing")
        path = Path(revision["artifact_directory"]) / video["path"]
        rel, video_bytes = _read_snapshot(root, path, video.get("sha256"), files)
        result["input_binding"] = {"status": "passed", "run_id":run["id"], "revision_id":revision["id"],
                                   "skill_id":variables["video_skill"], "collection_id":variables["collection_a"],
                                   "bgm_id":variables["bgm_material"], "video_path":rel,
                                   "video_sha256":video["sha256"], "image_sha256":[x["checksum"] for x in items],
                                   "audio_sha256":audios[0]["sha256"],
                                   "limitation":"Frozen input binding is not evidence of actual use in the finished video."}
        tool = ffmpeg or media_tool("ffmpeg")
        # Decode only the bytes just hash-verified. Do not reopen mutable
        # source paths after verification (a file could change in between).
        with tempfile.TemporaryDirectory(prefix="benchmark-video-evidence-") as temporary:
            temp = Path(temporary)
            video_copy, audio_copy = temp / "input.mp4", temp / "audio.wav"
            video_copy.write_bytes(video_bytes)
            audio_copy.write_bytes(audio_bytes)
            for name, inspect, inputs in (("frames", _video_samples, refs),
                                          ("full_frames", _video_all_frames, refs),
                                          ("audio", _audio_samples, audio_copy)):
                try:
                    result[name] = inspect(tool, video_copy, inputs)
                except (KeyError, TypeError, ValueError, OSError, subprocess.TimeoutExpired, UnavailableVisualEvidence) as error:
                    result[name] = {"assessment": "unknown", "error": {"kind": type(error).__name__, "message": str(error)}}
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError, zipfile.BadZipFile,
            subprocess.TimeoutExpired, UnavailableVisualEvidence) as error:
        result["error"] = {"kind":type(error).__name__, "message":str(error)}
    return result
