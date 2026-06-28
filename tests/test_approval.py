from video_review_os.approval import approval_key


def test_approval_key_is_stable_for_same_source_range_and_text():
    candidate = {
        "source_sha256": "abc",
        "start": 1.0,
        "end": 9.5,
        "text_sha256": "text",
        "text": "same",
    }
    assert approval_key(candidate) == approval_key(dict(candidate))


def test_approval_key_changes_when_range_changes():
    candidate = {
        "source_sha256": "abc",
        "start": 1.0,
        "end": 9.5,
        "text_sha256": "text",
        "text": "same",
    }
    changed = dict(candidate)
    changed["end"] = 10.0
    assert approval_key(candidate) != approval_key(changed)

