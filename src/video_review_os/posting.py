from __future__ import annotations

from pathlib import Path
from typing import Any

from .approval import approval_status, load_approvals
from .utils import atomic_write_json, read_json, utc_now_iso


def create_post_queue(
    project_dir: Path,
    *,
    platform: str = "generic",
    include_unapproved: bool = False,
) -> Path:
    source = read_json(project_dir / "source.json")
    clips = read_json(project_dir / "clips.json")
    approvals = load_approvals(project_dir)
    drafts = _drafts_by_clip(project_dir)
    renders = _renders_by_clip(project_dir)
    items = []

    for candidate in clips.get("candidates", []):
        if candidate.get("decision") == "reject":
            continue

        status = approval_status(candidate, approvals)
        render = renders.get(candidate["clip_id"])
        ready = status == "approved" and render is not None
        if not ready and not include_unapproved:
            continue

        item_status = "ready_for_manual_post" if ready else "blocked"
        reasons = []
        if status != "approved":
            reasons.append("clip_not_approved")
        if render is None:
            reasons.append("render_missing")
        draft = drafts.get(candidate["clip_id"], {})
        items.append(
            {
                "clip_id": candidate["clip_id"],
                "platform": platform,
                "status": item_status,
                "blocked_reasons": reasons,
                "media_path": render.get("output_path") if render else None,
                "title": draft.get("title"),
                "caption": draft.get("caption"),
                "hook": draft.get("hook"),
                "source_sha256": candidate.get("source_sha256"),
                "start": candidate.get("start"),
                "end": candidate.get("end"),
            }
        )

    artifact = {
        "schema_version": "video_review_os.post_queue.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "platform": platform,
        "auto_publish_enabled": False,
        "requires_explicit_adapter": True,
        "items": items,
    }
    out = project_dir / "post_queue.json"
    atomic_write_json(out, artifact)
    return out


def _drafts_by_clip(project_dir: Path) -> dict[str, dict[str, Any]]:
    path = project_dir / "drafts" / "copy.json"
    if not path.exists():
        return {}
    artifact = read_json(path)
    return {draft["clip_id"]: draft for draft in artifact.get("drafts", [])}


def _renders_by_clip(project_dir: Path) -> dict[str, dict[str, Any]]:
    path = project_dir / "renders.json"
    if not path.exists():
        return {}
    artifact = read_json(path)
    return {
        render["clip_id"]: render
        for render in artifact.get("renders", [])
        if render.get("status") == "rendered" and render.get("output_path")
    }
