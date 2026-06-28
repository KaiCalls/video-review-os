from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import atomic_write_json, read_json, sha256_text, utc_now_iso


def approval_key(candidate: dict[str, Any]) -> str:
    payload = "|".join(
        [
            str(candidate.get("source_sha256", "")),
            f"{float(candidate.get('start', 0.0)):.3f}",
            f"{float(candidate.get('end', 0.0)):.3f}",
            str(candidate.get("text_sha256") or sha256_text(str(candidate.get("text", "")))),
        ]
    )
    return sha256_text(payload)


def load_approvals(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "approvals.json"
    if not path.exists():
        return {"schema_version": "video_review_os.approvals.v1", "approvals": []}
    return read_json(path)


def approval_status(candidate: dict[str, Any], approvals: dict[str, Any]) -> str:
    key = approval_key(candidate)
    for approval in approvals.get("approvals", []):
        if approval.get("approval_key") == key:
            return str(approval.get("status", "unknown"))
    return "unreviewed"


def approve_clip(project_dir: Path, clip_id: str, reviewer: str = "local-reviewer", notes: str = "") -> Path:
    clips = read_json(project_dir / "clips.json")
    candidate = next((item for item in clips.get("candidates", []) if item.get("clip_id") == clip_id), None)
    if candidate is None:
        raise ValueError(f"Clip not found: {clip_id}")
    approvals = load_approvals(project_dir)
    key = approval_key(candidate)
    approvals["approvals"] = [
        approval for approval in approvals.get("approvals", []) if approval.get("approval_key") != key
    ]
    approvals["approvals"].append(
        {
            "approval_key": key,
            "clip_id": clip_id,
            "status": "approved",
            "reviewer": reviewer,
            "notes": notes,
            "approved_at": utc_now_iso(),
            "source_sha256": candidate["source_sha256"],
            "start": candidate["start"],
            "end": candidate["end"],
            "text_sha256": candidate["text_sha256"],
            "publish_allowed": False,
        }
    )
    out = project_dir / "approvals.json"
    atomic_write_json(out, approvals)
    return out

