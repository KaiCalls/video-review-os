from video_review_os.config import TaggingConfig, VideoReviewConfig
from video_review_os.tagging import _sanitize_tags, evaluate_source, tag_project
from video_review_os.utils import atomic_write_json, read_json


def source(filename="clip.mp4", width=1080, height=1920, audio_codec="aac", sha="a" * 64):
    return {
        "project_id": "clip-aaaaaaaaaaaa",
        "source": {"filename": filename, "original_path": f"/drive/{filename}", "sha256": sha},
        "media": {
            "duration_seconds": 42.0,
            "video": {"width": width, "height": height},
            "audio": {"codec": audio_codec},
        },
    }


def transcript(text=""):
    return {"text": text, "words": []}


def test_vertical_source_is_usable_vertical():
    tags = evaluate_source(source(width=1080, height=1920), transcript())
    assert tags["orientation"] == "vertical"
    assert tags["usable_vertical"] is True
    assert "horizontal_needs_reframe" not in tags["quality_flags"]


def test_horizontal_source_flags_reframe():
    tags = evaluate_source(source(width=1920, height=1080), transcript())
    assert tags["orientation"] == "horizontal"
    assert tags["usable_vertical"] is False
    assert "horizontal_needs_reframe" in tags["quality_flags"]


def test_event_type_from_transcript_keywords():
    tags = evaluate_source(source(), transcript("the bride walked down the aisle during the ceremony"))
    assert tags["event_type"] == "wedding"


def test_no_audio_and_no_transcript_flagged():
    tags = evaluate_source(source(audio_codec=None), transcript())
    assert tags["has_vocal"] is False
    assert "no_audio_stream" in tags["quality_flags"]
    assert "no_transcript" in tags["quality_flags"]


def test_roster_match_fills_performer_and_vocal_performer():
    cfg = TaggingConfig(roster=("Olivia",))
    tags = evaluate_source(source(filename="olivia-first-dance.mp4"), transcript("first dance"), cfg)
    assert tags["performer"] == "Olivia"
    assert tags["vocal_performer"] == "Olivia"


def test_real_gfa_bucket_for_identified_performance():
    cfg = TaggingConfig(roster=("Olivia",))
    tags = evaluate_source(
        source(filename="olivia-wedding.mp4"),
        transcript("Olivia sings at the wedding reception"),
        cfg,
    )
    assert tags["bucket"] == "real_gfa"


def test_default_bucket_when_no_signal():
    tags = evaluate_source(source(filename="broll.mp4"), transcript())
    assert tags["bucket"] == "broll_trending"


def test_every_record_has_a_bucket():
    tags = evaluate_source(source(audio_codec=None), transcript())
    assert tags["bucket"] in TaggingConfig().buckets


def test_sanitize_rejects_unknown_bucket_and_orientation():
    deterministic = evaluate_source(source(), transcript())
    raw = {"bucket": "not_a_bucket", "orientation": "diagonal", "event_type": "gala"}
    cleaned = _sanitize_tags(raw, deterministic, TaggingConfig())
    assert cleaned["bucket"] == deterministic["bucket"]
    assert cleaned["orientation"] == deterministic["orientation"]
    assert cleaned["event_type"] == "gala"  # free-text field passes through


def test_tag_project_round_trip(tmp_path):
    project = tmp_path / "clip-aaaaaaaaaaaa"
    project.mkdir()
    atomic_write_json(project / "source.json", source())
    atomic_write_json(project / "transcript.json", transcript("the ceremony begins"))
    out = tag_project(project, VideoReviewConfig())
    artifact = read_json(out)
    assert artifact["schema_version"] == "video_review_os.tags.v1"
    assert artifact["provider"] == "fallback"
    assert artifact["tags"]["event_type"] == "wedding"
    assert artifact["tags"]["bucket"] in artifact["buckets"]
