import json

from video_review_os.approval import approve_clip
from video_review_os.posting import create_post_queue
from video_review_os.utils import sha256_text


def test_post_queue_only_marks_approved_rendered_clips_ready(tmp_path):
    _write_project(tmp_path)
    approve_clip(tmp_path, "clip-001")

    out = create_post_queue(tmp_path, platform="shorts")
    artifact = json.loads(out.read_text(encoding="utf-8"))

    assert artifact["auto_publish_enabled"] is False
    assert artifact["requires_explicit_adapter"] is True
    assert len(artifact["items"]) == 1
    assert artifact["items"][0]["clip_id"] == "clip-001"
    assert artifact["items"][0]["status"] == "ready_for_manual_post"
    assert artifact["items"][0]["platform"] == "shorts"


def test_post_queue_can_include_blocked_items_for_review(tmp_path):
    _write_project(tmp_path)

    out = create_post_queue(tmp_path, include_unapproved=True)
    artifact = json.loads(out.read_text(encoding="utf-8"))

    blocked = {item["clip_id"]: item for item in artifact["items"]}
    assert blocked["clip-001"]["blocked_reasons"] == ["clip_not_approved"]
    assert blocked["clip-002"]["blocked_reasons"] == ["clip_not_approved", "render_missing"]
    assert "clip-003" not in blocked


def _write_project(project_dir):
    (project_dir / "drafts").mkdir()
    (project_dir / "source.json").write_text(
        json.dumps(
            {
                "project_id": "sample",
                "source": {"sha256": "source-hash", "filename": "sample.mp4"},
            }
        ),
        encoding="utf-8",
    )
    candidates = [
        _candidate("clip-001", "keep"),
        _candidate("clip-002", "review"),
        _candidate("clip-003", "reject"),
    ]
    (project_dir / "clips.json").write_text(
        json.dumps({"project_id": "sample", "source_sha256": "source-hash", "candidates": candidates}),
        encoding="utf-8",
    )
    (project_dir / "drafts" / "copy.json").write_text(
        json.dumps(
            {
                "drafts": [
                    {
                        "clip_id": "clip-001",
                        "title": "Useful clip",
                        "caption": "Caption draft",
                        "hook": "Hook draft",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "renders.json").write_text(
        json.dumps(
            {
                "renders": [
                    {
                        "clip_id": "clip-001",
                        "status": "rendered",
                        "output_path": str(project_dir / "renders" / "clip-001-keep.mp4"),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _candidate(clip_id, decision):
    text = f"{clip_id} transcript"
    return {
        "clip_id": clip_id,
        "decision": decision,
        "source_sha256": "source-hash",
        "start": 1.0,
        "end": 20.0,
        "text": text,
        "text_sha256": sha256_text(text),
    }
