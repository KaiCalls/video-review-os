from video_review_os.quality_gate import evaluate_candidate, score_copy_text


def candidate(text, start=0.0, end=24.0, words=None, media_duration=120.0):
    return {
        "clip_id": "clip-001",
        "source_sha256": "source",
        "start": start,
        "end": end,
        "text": text,
        "words": words or [],
        "media_duration_seconds": media_duration,
    }


def test_clean_complete_clip_is_keep():
    result = evaluate_candidate(
        candidate("Specific editing decisions make a draft easier to review before anyone publishes it.")
    )
    assert result["decision"] == "keep"
    assert result["score"] >= 80


def test_slate_marker_is_rejected():
    result = evaluate_candidate(candidate("cut five start again this one is not the usable take"))
    assert result["decision"] == "reject"
    assert any(flag["id"] == "slate_or_outtake_marker" for flag in result["flags"])


def test_mid_thought_opening_is_flagged():
    result = evaluate_candidate(candidate("and that is why the clip needs more setup before it stands alone"))
    assert any(flag["id"] == "starts_mid_thought" for flag in result["flags"])
    assert result["decision"] in {"trim", "review"}


def test_filler_heavy_opening_is_flagged():
    result = evaluate_candidate(candidate("um uh the useful part starts after the speaker restarts the thought"))
    assert any(flag["id"] == "filler_heavy_opening" for flag in result["flags"])


def test_awkward_pause_is_flagged_from_word_timestamps():
    words = [
        {"word": "A", "start": 0.0, "end": 0.2},
        {"word": "long", "start": 3.1, "end": 3.5},
        {"word": "pause", "start": 3.6, "end": 4.0},
    ]
    result = evaluate_candidate(candidate("A long pause happens here", words=words))
    assert any(flag["id"] == "awkward_pause" for flag in result["flags"])


def test_generic_hook_copy_is_flagged():
    result = score_copy_text("You need to hear this", "hook")
    assert not result["ok"]
    assert any(flag["id"] == "generic_low_value_hook" for flag in result["flags"])

