from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .config import VideoReviewConfig
from .utils import atomic_write_json, ensure_dir, read_json, utc_now_iso


def extract_project_scenes(
    project_dir: Path,
    config: VideoReviewConfig,
    *,
    dry_run: bool = False,
    strict: bool = False,
) -> Path:
    source = read_json(project_dir / "source.json")
    clips = read_json(project_dir / "clips.json")
    video_path = Path(source["source"]["active_path"])
    include = set(config.scenes.include_decisions)
    scenes = []

    for candidate in clips.get("candidates", []):
        decision = str(candidate.get("decision", ""))
        if decision not in include:
            scenes.append(
                {
                    "clip_id": candidate["clip_id"],
                    "decision": decision,
                    "status": "skipped",
                    "reason": "Decision is not configured for scene frames.",
                }
            )
            continue

        clip_dir = ensure_dir(project_dir / "scenes" / candidate["clip_id"])
        frame_records = []
        for idx, timestamp in enumerate(
            scene_frame_times(
                float(candidate["start"]),
                float(candidate["end"]),
                config.scenes.frames_per_clip,
            ),
            start=1,
        ):
            frame_path = clip_dir / f"frame-{idx:03d}.{config.scenes.image_extension}"
            status = "planned"
            error = None
            if not dry_run:
                try:
                    extract_frame(video_path, frame_path, timestamp, config)
                    status = "written"
                except RuntimeError as exc:
                    status = "error"
                    error = str(exc)
                    if strict:
                        raise
            frame_records.append(
                {
                    "index": idx,
                    "timestamp": round(timestamp, 3),
                    "path": str(frame_path),
                    "status": status,
                    "error": error,
                }
            )
        scenes.append(
            {
                "clip_id": candidate["clip_id"],
                "decision": decision,
                "status": "planned" if dry_run else "processed",
                "frames": frame_records,
            }
        )

    artifact = {
        "schema_version": "video_review_os.scenes.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "frames_per_clip": config.scenes.frames_per_clip,
        "include_decisions": list(config.scenes.include_decisions),
        "scenes": scenes,
    }
    out = project_dir / "scenes.json"
    atomic_write_json(out, artifact)
    return out


def scene_frame_times(start: float, end: float, count: int) -> list[float]:
    duration = max(0.0, end - start)
    if count <= 0 or duration <= 0:
        return []
    if count == 1:
        return [round(start + min(0.5, duration / 2), 3)]
    safe_start = start + min(0.25, duration / 4)
    safe_end = end - min(0.25, duration / 4)
    if count == 2:
        return [round(safe_start, 3), round(safe_end, 3)]
    step = (safe_end - safe_start) / (count - 1) if count > 1 else 0.0
    return [round(safe_start + (step * idx), 3) for idx in range(count)]


def extract_frame(video_path: Path, output_path: Path, timestamp: float, config: VideoReviewConfig) -> None:
    ensure_dir(output_path.parent)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    command = [
        config.media.ffmpeg_path,
        "-hide_banner",
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(tmp_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"ffmpeg not found: {config.media.ffmpeg_path}") from exc
    except subprocess.CalledProcessError as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(exc.stderr.strip() or str(exc)) from exc
    os.replace(tmp_path, output_path)
