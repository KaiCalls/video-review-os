"""End-to-end render proof. Skipped automatically when ffmpeg/ffprobe are unavailable."""

import json
import shutil
import subprocess

import pytest

from video_review_os.assembly import build_assemblies
from video_review_os.config import VideoReviewConfig
from video_review_os.render import render_assemblies

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def _make_video(path, seconds=20):
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-y",
            "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )


def _duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


def test_multi_range_assembly_renders_a_real_mp4(tmp_path):
    video = tmp_path / "source.mp4"
    _make_video(video, seconds=20)

    (tmp_path / "source.json").write_text(
        json.dumps(
            {
                "project_id": "demo",
                "source": {"sha256": "hash", "filename": "source.mp4", "active_path": str(video)},
                "media": {"duration_seconds": 20.0},
            }
        ),
        encoding="utf-8",
    )

    def cand(cid, dec, s, e, ops):
        return {
            "clip_id": cid, "project_id": "demo", "source_sha256": "hash",
            "start": s, "end": e, "text": cid, "text_sha256": f"t-{cid}",
            "quality_gate": {"decision": dec, "repair_ops": ops},
        }

    clips = {
        "project_id": "demo",
        "source_sha256": "hash",
        "candidates": [
            cand("clip-001", "keep", 1.0, 9.0, [{"op": "trim_end", "to": 7.0}]),
            cand("clip-002", "trim", 12.0, 19.0, [{"op": "drop_span", "start": 14.0, "end": 16.0}]),
            cand("clip-003", "reject", 19.0, 20.0, []),
        ],
    }
    (tmp_path / "clips.json").write_text(json.dumps(clips), encoding="utf-8")

    storyboard = {
        "schema_version": "video_review_os.storyboard.v1",
        "provider": "test",
        "status": "ok",
        "assemblies": [
            {
                "assembly_id": "asm-001",
                "rationale": "combine + bridge",
                "ordering": ["clip-001", "clip-002"],
                "title_card": {"text": "How we cut this"},
                "bridge_cards": [{"after_clip_id": "clip-001", "kind": "bridge", "text": "Six weeks later"}],
            }
        ],
    }
    (tmp_path / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")

    config = VideoReviewConfig()
    build_assemblies(tmp_path, config)
    out = render_assemblies(tmp_path, config)
    artifact = json.loads(out.read_text(encoding="utf-8"))

    assert artifact["renders"][0]["status"] == "rendered"
    output_path = tmp_path / "renders" / "assemblies" / "asm-001.mp4"
    assert output_path.exists()
    # title 1.6 + (7-1) + bridge 1.6 + (14-12) + (16-... wait) => combine: 1.6+6+1.6+2+3 = 14.2s
    duration = _duration(output_path)
    assert 13.0 < duration < 16.0
