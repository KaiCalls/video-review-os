from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

from .config import VideoReviewConfig
from .media import ffprobe_media
from .utils import atomic_write_json, ensure_dir, is_video_file, sha256_file, slugify, utc_now_iso


def discover_videos(watch_dir: Path) -> list[Path]:
    if not watch_dir.exists():
        return []
    return sorted(path for path in watch_dir.rglob("*") if is_video_file(path))


def ingest_many(paths: Iterable[Path], config: VideoReviewConfig) -> list[Path]:
    return [ingest_video(path, config) for path in paths]


def ingest_video(video_path: Path, config: VideoReviewConfig) -> Path:
    source = video_path.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if not is_video_file(source):
        raise ValueError(f"Not a supported video file: {source}")

    source_hash = sha256_file(source)
    project_id = f"{slugify(source.stem)}-{source_hash[:12]}"
    project_dir = ensure_dir(config.paths.projects_dir / project_id)
    media = ffprobe_media(source, config.media.ffprobe_path)

    source_ref = str(source)
    project_source_path: str | None = None
    if config.media.copy_source_to_project:
        copied = project_dir / "source" / source.name
        ensure_dir(copied.parent)
        if not copied.exists():
            tmp = copied.with_suffix(copied.suffix + ".tmp")
            shutil.copy2(source, tmp)
            tmp.replace(copied)
        project_source_path = str(copied)
        source_ref = project_source_path

    artifact = {
        "schema_version": "video_review_os.source.v1",
        "created_at": utc_now_iso(),
        "project_id": project_id,
        "source": {
            "original_path": str(source),
            "project_path": project_source_path,
            "active_path": source_ref,
            "filename": source.name,
            "sha256": source_hash,
            "size_bytes": source.stat().st_size,
            "mtime": source.stat().st_mtime,
        },
        "media": media,
        "safety": {
            "source_deleted": False,
            "source_moved": False,
            "auto_publish_enabled": False,
        },
    }
    atomic_write_json(project_dir / "source.json", artifact)
    ensure_dir(project_dir / "drafts")
    ensure_dir(project_dir / "renders")
    return project_dir

