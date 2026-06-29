import json

from video_review_os.approval import approve_assembly
from video_review_os.posting import create_assembly_post_queue


def _write_project(project_dir):
    (project_dir / "drafts").mkdir()
    (project_dir / "source.json").write_text(
        json.dumps({"project_id": "sample", "source": {"sha256": "src", "filename": "s.mp4"}}),
        encoding="utf-8",
    )
    assemblies = {
        "assemblies": [
            {
                "assembly_id": "asm-001",
                "kind": "single",
                "renderable": True,
                "member_decisions": ["keep"],
                "source_clip_ids": ["clip-001"],
                "segments": [{"kind": "source", "source_sha256": "src", "start": 0.0, "end": 5.0, "from_clip_id": "clip-001"}],
                "total_duration": 5.0,
                "assembly_signature": "sig-asm-001",
            }
        ]
    }
    (project_dir / "assemblies.json").write_text(json.dumps(assemblies), encoding="utf-8")
    (project_dir / "assembly_renders.json").write_text(
        json.dumps(
            {
                "renders": [
                    {
                        "assembly_id": "asm-001",
                        "status": "rendered",
                        "assembly_signature": "sig-asm-001",
                        "output_path": str(project_dir / "renders" / "assemblies" / "asm-001.mp4"),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "drafts" / "copy.json").write_text(
        json.dumps({"drafts": [{"clip_id": "clip-001", "title": "T", "caption": "C", "hook": "H"}]}),
        encoding="utf-8",
    )


def test_assembly_queue_ready_only_when_approved_and_rendered(tmp_path):
    _write_project(tmp_path)
    approve_assembly(tmp_path, "asm-001")

    out = create_assembly_post_queue(tmp_path, platform="shorts")
    artifact = json.loads(out.read_text(encoding="utf-8"))

    assert artifact["auto_publish_enabled"] is False
    assert artifact["requires_explicit_adapter"] is True
    assert len(artifact["items"]) == 1
    item = artifact["items"][0]
    assert item["assembly_id"] == "asm-001"
    assert item["status"] == "ready_for_manual_post"
    assert item["platform"] == "shorts"
    assert item["title"] == "T"


def test_assembly_queue_blocks_unapproved_when_requested(tmp_path):
    _write_project(tmp_path)

    out = create_assembly_post_queue(tmp_path, include_unapproved=True)
    artifact = json.loads(out.read_text(encoding="utf-8"))

    item = artifact["items"][0]
    assert item["status"] == "blocked"
    assert "assembly_not_approved" in item["blocked_reasons"]


def test_assembly_queue_blocks_a_stale_render(tmp_path):
    _write_project(tmp_path)
    # The EDL changed after it was rendered: signature no longer matches the render record.
    path = tmp_path / "assemblies.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["assemblies"][0]["assembly_signature"] = "sig-CHANGED"
    path.write_text(json.dumps(data), encoding="utf-8")
    approve_assembly(tmp_path, "asm-001")

    out = create_assembly_post_queue(tmp_path, include_unapproved=True)
    artifact = json.loads(out.read_text(encoding="utf-8"))

    item = artifact["items"][0]
    assert item["status"] == "blocked"
    assert "render_stale" in item["blocked_reasons"]
    assert "assembly_not_approved" not in item["blocked_reasons"]
