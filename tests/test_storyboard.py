from video_review_os.config import VideoReviewConfig
from video_review_os.storyboard import (
    FallbackStoryboardProvider,
    _sanitize_assemblies,
    fallback_storyboard,
    provider_for,
)


def _clips():
    # Long, non-adjacent clips so the combiner leaves them as separate assemblies.
    return {
        "project_id": "p",
        "source_sha256": "s",
        "candidates": [
            {"clip_id": "clip-001", "decision": "keep", "source_sha256": "s", "start": 0.0, "end": 25.0, "quality_gate": {"repair_ops": []}},
            {"clip_id": "clip-002", "decision": "reject", "source_sha256": "s", "start": 26.0, "end": 30.0, "quality_gate": {"repair_ops": []}},
            {"clip_id": "clip-003", "decision": "trim", "source_sha256": "s", "start": 100.0, "end": 130.0, "quality_gate": {"repair_ops": [{"op": "trim_end", "to": 120.0}]}},
        ],
    }


def test_fallback_makes_one_assembly_per_non_reject_clip():
    storyboard = fallback_storyboard(_clips(), VideoReviewConfig())
    ordering = [a["ordering"][0] for a in storyboard["assemblies"]]
    assert ordering == ["clip-001", "clip-003"]
    assert storyboard["provider"] == "fallback"
    assert storyboard["status"] == "fallback"


def test_combiner_merges_a_mid_thought_clip_with_its_lead_in():
    clips = {
        "project_id": "p",
        "source_sha256": "s",
        "candidates": [
            {"clip_id": "clip-001", "decision": "keep", "source_sha256": "s", "start": 0.0, "end": 20.0, "quality_gate": {"repair_ops": []}},
            {
                "clip_id": "clip-002",
                "decision": "trim",
                "source_sha256": "s",
                "start": 20.5,
                "end": 40.0,
                "quality_gate": {"repair_ops": [{"op": "add_lead_in", "needs_prior_range": True}]},
            },
        ],
    }
    storyboard = fallback_storyboard(clips, VideoReviewConfig())
    assert len(storyboard["assemblies"]) == 1
    assert storyboard["assemblies"][0]["ordering"] == ["clip-001", "clip-002"]


def test_combine_disabled_keeps_clips_separate():
    from dataclasses import replace

    clips = {
        "project_id": "p",
        "source_sha256": "s",
        "candidates": [
            {"clip_id": "clip-001", "decision": "keep", "source_sha256": "s", "start": 0.0, "end": 20.0, "quality_gate": {"repair_ops": []}},
            {
                "clip_id": "clip-002",
                "decision": "trim",
                "source_sha256": "s",
                "start": 20.5,
                "end": 40.0,
                "quality_gate": {"repair_ops": [{"op": "add_lead_in"}]},
            },
        ],
    }
    config = VideoReviewConfig()
    config = replace(config, assembly=replace(config.assembly, combine=False))
    storyboard = fallback_storyboard(clips, config)
    assert len(storyboard["assemblies"]) == 2


def test_default_provider_is_fallback():
    assert isinstance(provider_for(VideoReviewConfig().storyboard), FallbackStoryboardProvider)


def test_sanitize_drops_reject_and_unknown_members_from_hosted_output():
    raw = [
        {
            "ordering": ["clip-001", "clip-002", "ghost"],
            "rationale": "combine",
            "bridge_cards": [{"after_clip_id": "clip-001", "kind": "bridge", "text": "later"}],
            "title_card": {"text": "Title"},
        }
    ]
    cleaned = _sanitize_assemblies(raw, _clips(), VideoReviewConfig())
    assert len(cleaned) == 1
    assert cleaned[0]["ordering"] == ["clip-001"]  # reject + unknown removed
    assert len(cleaned[0]["bridge_cards"]) == 1
    assert cleaned[0]["title_card"]["text"] == "Title"


def test_sanitize_drops_assembly_with_only_reject_members():
    raw = [{"ordering": ["clip-002"], "rationale": "x"}]
    assert _sanitize_assemblies(raw, _clips(), VideoReviewConfig()) == []
