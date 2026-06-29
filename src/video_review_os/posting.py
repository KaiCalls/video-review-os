from __future__ import annotations

from pathlib import Path
from typing import Any

from .approval import approval_status, assembly_approval_status, load_approvals
from .assembly import assembly_is_renderable
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


def create_assembly_post_queue(
    project_dir: Path,
    *,
    platform: str = "generic",
    include_unapproved: bool = False,
) -> Path:
    """Manual post queue for auto-generated assembly drafts.

    An assembly is ready for manual post only when it is approved (signature match) and
    rendered. Reject members can never reach here. Nothing is published."""
    source = read_json(project_dir / "source.json")
    assemblies = read_json(project_dir / "assemblies.json")
    approvals = load_approvals(project_dir)
    drafts = _drafts_by_clip(project_dir)
    renders = _assembly_renders_by_id(project_dir)
    items = []

    for assembly in assemblies.get("assemblies", []):
        if not assembly_is_renderable(assembly):
            continue
        status = assembly_approval_status(assembly, approvals)
        render = renders.get(assembly["assembly_id"])
        signature = assembly.get("assembly_signature")
        # A render only counts if it was produced from the CURRENT edit (signature match).
        render_is_current = render is not None and render.get("assembly_signature") == signature
        ready = status == "approved" and render_is_current
        if not ready and not include_unapproved:
            continue

        item_status = "ready_for_manual_post" if ready else "blocked"
        reasons = []
        if status != "approved":
            reasons.append("assembly_not_approved")
        if render is None:
            reasons.append("render_missing")
        elif not render_is_current:
            reasons.append("render_stale")
        source_clip_ids = assembly.get("source_clip_ids", [])
        draft = drafts.get(source_clip_ids[0], {}) if source_clip_ids else {}
        items.append(
            {
                "assembly_id": assembly["assembly_id"],
                "platform": platform,
                "status": item_status,
                "blocked_reasons": reasons,
                "media_path": render.get("output_path") if render else None,
                "title": draft.get("title"),
                "caption": draft.get("caption"),
                "hook": draft.get("hook"),
                "source_clip_ids": source_clip_ids,
                "segment_count": len(assembly.get("segments", [])),
                "total_duration": assembly.get("total_duration"),
            }
        )

    artifact = {
        "schema_version": "video_review_os.assembly_post_queue.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "platform": platform,
        "auto_publish_enabled": False,
        "requires_explicit_adapter": True,
        "items": items,
    }
    out = project_dir / "assembly_post_queue.json"
    atomic_write_json(out, artifact)
    return out


def _assembly_renders_by_id(project_dir: Path) -> dict[str, dict[str, Any]]:
    path = project_dir / "assembly_renders.json"
    if not path.exists():
        return {}
    artifact = read_json(path)
    return {
        render["assembly_id"]: render
        for render in artifact.get("renders", [])
        if render.get("status") == "rendered" and render.get("output_path")
    }


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
