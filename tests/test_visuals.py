import json

from video_review_os.config import VideoReviewConfig, VisualConfig
from video_review_os.utils import sha256_text
from video_review_os.visuals import make_project_visuals


def test_visual_maker_uses_scene_frames_and_optional_mascot(tmp_path):
    frame = tmp_path / "frame.jpg"
    mascot = tmp_path / "mascot.png"
    frame.write_bytes(b"frame")
    mascot.write_bytes(b"mascot")
    _write_visual_project(tmp_path, frame)
    config = VideoReviewConfig(visuals=VisualConfig(mascot_image_path=mascot))

    out = make_project_visuals(tmp_path, config)
    artifact = json.loads(out.read_text(encoding="utf-8"))
    thumbnail = tmp_path / "visuals" / "thumbnails" / "clip-001.svg"
    scene_card = tmp_path / "visuals" / "scene-cards" / "clip-001.svg"

    assert artifact["visuals"][0]["uses_mascot"] is True
    assert artifact["visuals"][0]["frame_count"] == 1
    assert thumbnail.exists()
    assert scene_card.exists()
    assert "data:image/jpeg;base64" in thumbnail.read_text(encoding="utf-8")
    assert "data:image/png;base64" in thumbnail.read_text(encoding="utf-8")


def test_visual_maker_skips_rejects_by_default(tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    _write_visual_project(tmp_path, frame, decision="reject")

    out = make_project_visuals(tmp_path, VideoReviewConfig())
    artifact = json.loads(out.read_text(encoding="utf-8"))

    assert artifact["visuals"][0]["status"] == "skipped"


def _write_visual_project(project_dir, frame_path, decision="keep"):
    (project_dir / "drafts").mkdir()
    text = "This is the useful part of the clip."
    (project_dir / "source.json").write_text(
        json.dumps(
            {
                "project_id": "sample",
                "source": {"sha256": "source-hash", "filename": "sample.mp4"},
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "clips.json").write_text(
        json.dumps(
            {
                "project_id": "sample",
                "source_sha256": "source-hash",
                "candidates": [
                    {
                        "clip_id": "clip-001",
                        "decision": decision,
                        "source_sha256": "source-hash",
                        "start": 1.0,
                        "end": 20.0,
                        "text": text,
                        "text_sha256": sha256_text(text),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "scenes.json").write_text(
        json.dumps(
            {
                "scenes": [
                    {
                        "clip_id": "clip-001",
                        "frames": [
                            {
                                "index": 1,
                                "timestamp": 2.0,
                                "path": str(frame_path),
                                "status": "written",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "drafts" / "copy.json").write_text(
        json.dumps(
            {
                "drafts": [
                    {
                        "clip_id": "clip-001",
                        "title": "Useful thumbnail title",
                        "hook": "This should be clear before anyone posts it.",
                        "caption": "Caption draft",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
