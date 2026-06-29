import json

from video_review_os.captions import build_assembly_captions
from video_review_os.config import VideoReviewConfig


def _write_project(project_dir):
    (project_dir / "source.json").write_text(
        json.dumps({"project_id": "demo", "source": {"sha256": "h", "filename": "s.mp4"}}),
        encoding="utf-8",
    )
    candidates = [
        {
            "clip_id": "clip-001",
            "decision": "keep",
            "source_sha256": "h",
            "start": 1.0,
            "end": 7.0,
            "text": "hello world",
            "words": [
                {"word": "hello", "start": 1.0, "end": 1.5},
                {"word": "world", "start": 1.5, "end": 2.0},
            ],
        },
        {
            "clip_id": "clip-002",
            "decision": "trim",
            "source_sha256": "h",
            "start": 12.0,
            "end": 19.0,
            "text": "second clip",
            "words": [
                {"word": "second", "start": 12.0, "end": 12.5},
                {"word": "clip", "start": 12.5, "end": 13.0},
            ],
        },
    ]
    (project_dir / "clips.json").write_text(
        json.dumps({"project_id": "demo", "source_sha256": "h", "candidates": candidates}),
        encoding="utf-8",
    )
    assembly = {
        "assembly_id": "asm-001",
        "kind": "multi",
        "source_clip_ids": ["clip-001", "clip-002"],
        "member_decisions": ["keep", "trim"],
        "renderable": True,
        "segments": [
            {"kind": "card", "card_kind": "title", "text": "Title", "duration": 1.6},
            {"kind": "source", "source_sha256": "h", "start": 1.0, "end": 7.0, "from_clip_id": "clip-001"},
            {"kind": "card", "card_kind": "bridge", "text": "Later", "duration": 1.6},
            {"kind": "source", "source_sha256": "h", "start": 12.0, "end": 19.0, "from_clip_id": "clip-002"},
        ],
    }
    (project_dir / "assemblies.json").write_text(json.dumps({"assemblies": [assembly]}), encoding="utf-8")


def test_assembly_captions_offset_to_the_final_timeline(tmp_path):
    _write_project(tmp_path)
    out = build_assembly_captions(tmp_path, VideoReviewConfig())
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["captions"][0]["cue_count"] >= 2

    full_srt = (tmp_path / "captions" / "assemblies" / "asm-001.srt").read_text(encoding="utf-8")
    # clip-001 starts after the 1.6s title card.
    assert "00:00:01,600 -->" in full_srt
    # clip-002 starts after title(1.6) + clip-001(6.0) + bridge(1.6) = 9.2s.
    assert "00:00:09,200 -->" in full_srt


def test_per_segment_captions_are_segment_local(tmp_path):
    _write_project(tmp_path)
    build_assembly_captions(tmp_path, VideoReviewConfig())
    # Segment index 4 is clip-002 (card, source, card, source) -> segment-local starts at 0.
    seg_srt = (tmp_path / "captions" / "assemblies" / "asm-001" / "seg-004.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 -->" in seg_srt
