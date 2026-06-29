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


def _op(result, kind):
    return next((op for op in result["repair_ops"] if op["op"] == kind), None)


def test_clean_clip_emits_no_repair_ops():
    result = evaluate_candidate(
        candidate("Specific editing decisions make a draft easier to review before anyone publishes it.")
    )
    assert result["repair_ops"] == []


def test_awkward_pause_emits_a_drop_span_op_with_gap_bounds():
    words = [
        {"word": "A", "start": 0.0, "end": 0.2},
        {"word": "long", "start": 3.1, "end": 3.5},
        {"word": "pause", "start": 3.6, "end": 4.0},
    ]
    result = evaluate_candidate(candidate("A long pause happens here", words=words))
    op = _op(result, "drop_span")
    assert op is not None
    assert op["start"] == 0.2
    assert op["end"] == 3.1


def test_weak_ending_emits_a_trim_end_op():
    words = [
        {"word": "this", "start": 0.0, "end": 1.0},
        {"word": "point", "start": 1.0, "end": 2.0},
        {"word": "matters", "start": 2.0, "end": 3.0},
        {"word": "and", "start": 3.0, "end": 4.0},
    ]
    result = evaluate_candidate(candidate("this point matters and", words=words))
    op = _op(result, "trim_end")
    assert op is not None
    assert op["to"] == 3.0  # end of the second-to-last word


def test_mid_thought_emits_add_lead_in_suggestion():
    result = evaluate_candidate(candidate("and that is why the clip needs more setup before it stands alone"))
    assert any(op["op"] == "add_lead_in" for op in result["repair_ops"])

