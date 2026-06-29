from video_review_os.scenes import scene_frame_times


def test_scene_frame_times_sample_start_middle_and_end():
    assert scene_frame_times(10.0, 40.0, 3) == [10.25, 25.0, 39.75]


def test_scene_frame_times_handle_short_single_frame_clip():
    assert scene_frame_times(5.0, 5.4, 1) == [5.2]


def test_scene_frame_times_return_empty_for_zero_duration():
    assert scene_frame_times(5.0, 5.0, 3) == []
