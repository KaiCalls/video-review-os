import json

from dataclasses import replace

from video_review_os.config import VideoReviewConfig
from video_review_os.render import _build_assembly_filtergraph, _wrap_card_text, plan_assembly_render, render_assemblies


def test_wrap_card_text_wraps_and_caps_lines():
    wrapped = _wrap_card_text("the quick brown fox jumps over the lazy dog again and again", max_chars=12, max_lines=3)
    lines = wrapped.split("\n")
    assert len(lines) <= 3
    assert all(len(line) <= 14 for line in lines)


def test_filtergraph_is_single_pass_with_loudnorm(tmp_path):
    plan = {
        "segments": [
            {"kind": "source", "start": 0.0, "duration": 5.0, "intermediate": str(tmp_path / "s1.mp4"), "caption_path": str(tmp_path / "none.srt")},
            {"kind": "card", "duration": 1.6, "text": "Hi", "intermediate": str(tmp_path / "s2.mp4")},
            {"kind": "source", "start": 10.0, "duration": 4.0, "intermediate": str(tmp_path / "s3.mp4"), "caption_path": str(tmp_path / "none.srt")},
        ]
    }
    inputs, fc, audio_out = _build_assembly_filtergraph(tmp_path / "v.mp4", plan, VideoReviewConfig(), False)
    assert "concat=n=3:v=1:a=1[cv][ca]" in fc  # one-pass concat of all 3 segments
    assert "loudnorm=I=-14.0" in fc
    assert audio_out == "[cn]"  # no music configured
    assert inputs.count("-ss") == 2  # two source segments seek into the source


def test_filtergraph_burns_captions_and_mixes_music(tmp_path):
    caption = tmp_path / "seg.srt"
    caption.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    music = tmp_path / "m.m4a"
    music.write_bytes(b"x")
    config = VideoReviewConfig()
    config = replace(config, assembly=replace(config.assembly, music_path=str(music)))
    plan = {
        "segments": [
            {"kind": "source", "start": 0.0, "duration": 5.0, "intermediate": str(tmp_path / "s1.mp4"), "caption_path": str(caption)},
        ]
    }
    _inputs, fc, audio_out = _build_assembly_filtergraph(tmp_path / "v.mp4", plan, config, True)
    assert "subtitles=" in fc and "force_style=" in fc
    assert "amix=inputs=2" in fc
    assert audio_out == "[outa]"


def test_filtergraph_blur_mode_has_no_black_bars(tmp_path):
    plan = {"segments": [{"kind": "source", "start": 0.0, "duration": 5.0, "intermediate": str(tmp_path / "s1.mp4"), "caption_path": str(tmp_path / "none.srt")}]}
    _inputs, fc, _audio = _build_assembly_filtergraph(tmp_path / "v.mp4", plan, VideoReviewConfig(), False)
    assert "overlay=" in fc and "boxblur" in fc  # blurred-fill background, not letterbox pad


def _cfg():
    return VideoReviewConfig()


def test_single_source_segment_plans_a_single_pass(tmp_path):
    assembly = {
        "assembly_id": "asm-001",
        "kind": "single",
        "segments": [{"kind": "source", "source_sha256": "s", "start": 0.0, "end": 5.0, "from_clip_id": "c1"}],
    }
    plan = plan_assembly_render(assembly, tmp_path, _cfg())
    assert plan["single_pass"] is True
    assert len(plan["segments"]) == 1
    assert plan["segments"][0]["duration"] == 5.0


def test_multi_segment_with_card_is_not_single_pass(tmp_path):
    assembly = {
        "assembly_id": "asm-001",
        "kind": "multi",
        "segments": [
            {"kind": "source", "source_sha256": "s", "start": 0.0, "end": 5.0, "from_clip_id": "c1"},
            {"kind": "card", "card_kind": "bridge", "text": "later", "duration": 1.5},
            {"kind": "source", "source_sha256": "s", "start": 10.0, "end": 14.0, "from_clip_id": "c2"},
        ],
    }
    plan = plan_assembly_render(assembly, tmp_path, _cfg())
    assert plan["single_pass"] is False
    assert len(plan["segments"]) == 3
    assert plan["segments"][1]["kind"] == "card"


def test_render_assemblies_dry_run_skips_reject_member_assembly(tmp_path):
    (tmp_path / "source.json").write_text(
        json.dumps(
            {
                "project_id": "sample",
                "source": {"sha256": "src", "filename": "s.mp4", "active_path": str(tmp_path / "s.mp4")},
            }
        ),
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
            },
            {
                "assembly_id": "asm-002",
                "kind": "single",
                "renderable": True,
                "member_decisions": ["reject"],
                "source_clip_ids": ["clip-002"],
                "segments": [{"kind": "source", "source_sha256": "src", "start": 0.0, "end": 5.0, "from_clip_id": "clip-002"}],
                "total_duration": 5.0,
            },
        ]
    }
    (tmp_path / "assemblies.json").write_text(json.dumps(assemblies), encoding="utf-8")

    out = render_assemblies(tmp_path, _cfg(), dry_run=True)
    artifact = json.loads(out.read_text(encoding="utf-8"))
    by_id = {r["assembly_id"]: r for r in artifact["renders"]}

    assert artifact["reject_never_renders"] is True
    assert artifact["auto_publish_enabled"] is False
    assert by_id["asm-001"]["status"] == "dry-run"
    assert by_id["asm-002"]["status"] == "skipped"
