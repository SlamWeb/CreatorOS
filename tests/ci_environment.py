"""Canonical isolated CI temp roots and required real local codec preflight."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


def _is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def prepare_temp(workspace, job, github_env=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job):
        raise ValueError("Invalid CI job name")
    root = Path(workspace).resolve(strict=True)
    target = root
    for part in ("tmp", "ci-" + job):
        target /= part
        if target.exists() and _is_link(target):
            raise ValueError(f"CI temp directory is linked: {target}")
        target.mkdir(exist_ok=True)
    target = target.resolve(strict=True)
    target.relative_to(root)
    if github_env:
        with Path(github_env).open("a", encoding="utf-8") as stream:
            for key in ("TEMP", "TMP", "TMPDIR"):
                stream.write(f"{key}={target}\n")
    return target


def guarded_environment(directory):
    root = Path(directory)
    (root / "codex-home").mkdir(exist_ok=True)
    return {
        "CODEX_HOME": str(root / "codex-home"), "PYTHON_DOTENV_DISABLED": "1",
        "OPENAI_API_KEY": "", "DEEPSEEK_API_KEY": "", "CODEX_API_KEY": "", "ZHIHU_ACCESS_SECRET": "",
        "OPENAI_BASE_URL": "http://127.0.0.1:1/v1", "DEEPSEEK_BASE_URL": "http://127.0.0.1:1/v1",
        "PERSONCLONE_SESSION_COOKIE": "", "PERSONCLONE_BASE_URL": "http://127.0.0.1:1",
        "CREATOROS_XHS_MCP_TOKEN": "", "CREATOROS_XHS_MCP_URL": "http://127.0.0.1:1/mcp",
        "CREATOROS_XHS_MCP_TOKEN_FILE": str(root / "no-publication-token"),
        "CREATOROS_XHS_STAGING_ROOT": str(root / "publication-staging"),
    }


def _verify_media(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        value = os.environ.get("CREATOROS_" + name.upper()) or shutil.which(name)
        if not value or not Path(value).is_file():
            raise RuntimeError(f"Required media runtime missing: {name}")
        path = Path(value).resolve(strict=True)
        version = subprocess.run([str(path), "-version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", check=True, timeout=20)
        tools[name] = {"path": str(path), "version": version.stdout.splitlines()[0],
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    # Verify both binaries actually support the codecs used by the controlled tests.
    clip = directory / "codec-preflight.mp4"
    subprocess.run([tools["ffmpeg"]["path"], "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=32x48:r=10", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                    "-t", "0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)],
                   capture_output=True, check=True, timeout=30)
    probe = subprocess.run([tools["ffprobe"]["path"], "-v", "error", "-show_streams", "-show_format",
                            "-of", "json", str(clip)], capture_output=True, text=True,
                           encoding="utf-8", check=True, timeout=20)
    metadata = json.loads(probe.stdout)
    streams = metadata.get("streams", [])
    if not any(x.get("codec_type") == "video" and x.get("width") == 32 and x.get("height") == 48 for x in streams):
        raise RuntimeError("Media preflight did not produce the expected video")
    if not any(x.get("codec_type") == "audio" for x in streams) or float(metadata["format"]["duration"]) <= 0:
        raise RuntimeError("Media preflight did not produce valid audio/duration")
    receipt = {"status": "passed", "paid_calls": False, "tools": tools, "probe": metadata}
    (directory / "media-preflight.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def verify_media(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        return _verify_media(directory)
    except Exception as error:
        stderr = getattr(error, "stderr", None)
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        failure = {"status": "failed", "paid_calls": False,
                   "error": f"{type(error).__name__}: {error}", "stderr": stderr}
        (directory / "media-preflight.failure.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--workspace", required=True)
    prepare.add_argument("--job", required=True)
    prepare.add_argument("--github-env", required=True)
    media = commands.add_parser("media")
    media.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        root = prepare_temp(args.workspace, args.job, args.github_env)
        with Path(args.github_env).open("a", encoding="utf-8") as stream:
            for key, value in guarded_environment(root).items():
                stream.write(f"{key}={value}\n")
        print(root)
    else:
        print(json.dumps(verify_media(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
