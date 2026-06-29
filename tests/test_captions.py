from video_review_os.captions import build_caption_cues, cues_to_srt, cues_to_vtt


def test_caption_cues_use_clip_relative_word_times():
    candidate = {
        "start": 10.0,
        "end": 20.0,
        "words": [
            {"word": "This", "start": 10.2, "end": 10.5},
            {"word": "clip", "start": 10.6, "end": 10.9},
            {"word": "has", "start": 11.0, "end": 11.2},
        ],
    }

    cues = build_caption_cues(candidate, max_chars=42, max_seconds=3.5)

    assert cues == [
        {
            "index": 1,
            "start": 0.2,
            "end": 1.2,
            "text": "This clip has",
        }
    ]


def test_caption_cues_split_on_max_chars():
    candidate = {
        "start": 0.0,
        "end": 8.0,
        "words": [
            {"word": "Short", "start": 0.0, "end": 0.4},
            {"word": "caption", "start": 0.5, "end": 0.9},
            {"word": "lines", "start": 1.0, "end": 1.3},
        ],
    }

    cues = build_caption_cues(candidate, max_chars=12, max_seconds=3.5)

    assert [cue["text"] for cue in cues] == ["Short", "caption", "lines"]


def test_caption_formats_are_written_as_srt_and_vtt():
    cues = [{"index": 1, "start": 0.0, "end": 1.25, "text": "Hello"}]

    assert "00:00:00,000 --> 00:00:01,250" in cues_to_srt(cues)
    assert cues_to_vtt(cues).startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.250")
