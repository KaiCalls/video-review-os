from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .config import VideoReviewConfig
from .utils import atomic_write_json, ensure_dir, read_json, slugify, utc_now_iso


def is_renderable(candidate: dict[str, Any], include_decisions: Iterable[str] | None = None) -> bool:
    decision = str(candidate.get("decision", "")).lower()
    if decision == "reject":
        return False
    allowed = set(include_decisions or ("keep",))
    return decision in allowed


def render_project(
    project_dir: Path,
    config: VideoReviewConfig,
    include_decisions: Iterable[str] | None = None,
    dry_run: bool = False,
    burn_captions: bool = False,
) -> Path:
    source = read_json(project_dir / "source.json")
    clips = read_json(project_dir / "clips.json")
    video_path = Path(source["source"]["active_path"])
    include = tuple(include_decisions or config.render.default_decisions)
    renders = []
    for candidate in clips.get("candidates", []):
        if not is_renderable(candidate, include):
            renders.append(
                {
                    "clip_id": candidate["clip_id"],
                    "decision": candidate["decision"],
                    "status": "skipped",
                    "reason": "Decision is not renderable under current policy.",
                }
            )
            continue
        caption_path = project_dir / "captions" / f"{candidate['clip_id']}.srt"
        active_caption = caption_path if burn_captions and caption_path.exists() else None
        suffix = f"{candidate['clip_id']}-{slugify(candidate.get('decision', 'draft'))}"
        if active_caption:
            suffix = f"{suffix}-captioned"
        output = project_dir / "renders" / f"{suffix}.mp4"
        if dry_run:
            renders.append(
                {
                    "clip_id": candidate["clip_id"],
                    "decision": candidate["decision"],
                    "status": "dry-run",
                    "output_path": str(output),
                    "burned_captions": active_caption is not None,
                    "caption_path": str(active_caption) if active_caption else None,
                }
            )
            continue
        render_clip(
            video_path,
            output,
            float(candidate["start"]),
            float(candidate["end"]),
            config,
            caption_path=active_caption,
        )
        renders.append(
            {
                "clip_id": candidate["clip_id"],
                "decision": candidate["decision"],
                "status": "rendered",
                "output_path": str(output),
                "burned_captions": active_caption is not None,
                "caption_path": str(active_caption) if active_caption else None,
            }
        )
    artifact = {
        "schema_version": "video_review_os.renders.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "include_decisions": list(include),
        "reject_never_renders": True,
        "auto_publish_enabled": False,
        "burn_captions_requested": burn_captions,
        "renders": renders,
    }
    out = project_dir / "renders.json"
    atomic_write_json(out, artifact)
    return out


def render_clip(
    video_path: Path,
    output_path: Path,
    start: float,
    end: float,
    config: VideoReviewConfig,
    *,
    caption_path: Path | None = None,
) -> None:
    ensure_dir(output_path.parent)
    duration = max(0.0, end - start)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    subtitle_args = ["-vf", _subtitle_filter(caption_path)] if caption_path else []
    command = [
        config.media.ffmpeg_path,
        "-hide_banner",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration:.3f}",
        *subtitle_args,
        "-c:v",
        config.render.video_codec,
        "-preset",
        config.render.preset,
        "-crf",
        str(config.render.crf),
        "-c:a",
        config.render.audio_codec,
        "-movflags",
        "+faststart",
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


def _subtitle_filter(path: Path) -> str:
    value = path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
    return f"subtitles='{value}'"
