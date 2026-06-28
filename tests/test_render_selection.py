from video_review_os.render import is_renderable


def test_keep_renders_by_default():
    assert is_renderable({"decision": "keep"})


def test_trim_does_not_render_by_default():
    assert not is_renderable({"decision": "trim"})


def test_trim_renders_when_explicitly_included():
    assert is_renderable({"decision": "trim"}, include_decisions=["keep", "trim"])


def test_review_renders_when_explicitly_included():
    assert is_renderable({"decision": "review"}, include_decisions=["review"])


def test_reject_never_renders_even_if_included():
    assert not is_renderable({"decision": "reject"}, include_decisions=["reject"])

