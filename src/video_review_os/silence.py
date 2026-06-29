"""Audio-truth silence detection.

Transcript word-gaps miss real dead air — breaths, room tone, untranscribed pauses. This
runs ffmpeg ``silencedetect`` over the source audio once and writes the silent intervals so
the assembly layer can drop them in addition to transcript-gap repair ops. Deterministic
fallback: if ffmpeg is missing or fails, the interval list is simply empty.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import VideoReviewConfig
from .utils import atomic_write_json, read_json, utc_now_iso

_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def detect_silence(project_dir: Path, config: VideoReviewConfig) -> Path:
    source = read_json(project_dir / "source.json")
    video_path = Path(source["source"]["active_path"])
    intervals: list[dict[str, float]] = []
    status = "ok"
    errors: list[str] = []

    command = [
        config.media.ffmpeg_path,
        "-hide_banner",
        "-nostats",
        "-i",
        str(video_path),
        "-af",
        f"silencedetect=noise={config.assembly.silence_noise}:d={config.assembly.silence_min_seconds}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        intervals = _parse_silence(result.stderr or "")
    except FileNotFoundError:
        status = "fallback"
        errors.append(f"ffmpeg not found: {config.media.ffmpeg_path}")

    artifact = {
        "schema_version": "video_review_os.silence.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "status": status,
        "noise": config.assembly.silence_noise,
        "min_seconds": config.assembly.silence_min_seconds,
        "errors": errors,
        "intervals": intervals,
    }
    out = project_dir / "silence.json"
    atomic_write_json(out, artifact)
    return out


def _parse_silence(stderr: str) -> list[dict[str, float]]:
    intervals: list[dict[str, float]] = []
    pending: float | None = None
    for line in stderr.splitlines():
        start_match = _START_RE.search(line)
        if start_match:
            pending = float(start_match.group(1))
            continue
        end_match = _END_RE.search(line)
        if end_match and pending is not None:
            end = float(end_match.group(1))
            start = max(0.0, pending)
            if end > start:
                intervals.append({"start": round(start, 3), "end": round(end, 3)})
            pending = None
    return intervals


def load_silence_intervals(project_dir: Path) -> list[dict[str, float]]:
    path = project_dir / "silence.json"
    if not path.exists():
        return []
    return read_json(path).get("intervals", [])
