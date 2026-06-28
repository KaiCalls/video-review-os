from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def ffprobe_media(path: Path, ffprobe_path: str = "ffprobe") -> dict[str, Any]:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:
        return {"ok": False, "error": f"ffprobe not found: {ffprobe_path}"}
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "error": exc.stderr.strip() or str(exc)}

    raw = json.loads(result.stdout or "{}")
    duration = _float_or_none(raw.get("format", {}).get("duration"))
    video_stream = next(
        (stream for stream in raw.get("streams", []) if stream.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (stream for stream in raw.get("streams", []) if stream.get("codec_type") == "audio"),
        {},
    )
    return {
        "ok": True,
        "duration_seconds": duration,
        "format_name": raw.get("format", {}).get("format_name"),
        "bit_rate": _int_or_none(raw.get("format", {}).get("bit_rate")),
        "video": {
            "codec": video_stream.get("codec_name"),
            "width": _int_or_none(video_stream.get("width")),
            "height": _int_or_none(video_stream.get("height")),
            "r_frame_rate": video_stream.get("r_frame_rate"),
        },
        "audio": {
            "codec": audio_stream.get("codec_name"),
            "sample_rate": _int_or_none(audio_stream.get("sample_rate")),
            "channels": _int_or_none(audio_stream.get("channels")),
        },
    }


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None

