import json

from video_review_os.approval import approve_assembly, assembly_approval_status, load_approvals
from video_review_os.assembly import (
    assembly_is_renderable,
    assembly_signature,
    build_assemblies,
    resolve_clip_segments,
)
from video_review_os.config import VideoReviewConfig


def _cfg():
    return VideoReviewConfig()


def _candidate(clip_id, decision, start, end, ops=None):
    return {
        "clip_id": clip_id,
        "decision": decision,
        "source_sha256": "src",
        "start": start,
        "end": end,
        "text": clip_id,
        "text_sha256": f"t-{clip_id}",
        "quality_gate": {"decision": decision, "repair_ops": ops or []},
    }


def test_drop_span_splits_one_range_into_two_source_segments():
    candidate = _candidate("clip-001", "trim", 0.0, 30.0, [{"op": "drop_span", "start": 10.0, "end": 12.0}])
    segments, applied, _ = resolve_clip_segments(candidate, _cfg())
    assert len(segments) == 2
    assert (segments[0]["start"], segments[0]["end"]) == (0.0, 10.0)
    assert (segments[1]["start"], segments[1]["end"]) == (12.0, 30.0)
    assert applied and applied[0]["op"] == "drop_span"


def test_trim_start_and_end_shrink_the_single_segment():
    candidate = _candidate(
        "clip-001", "trim", 0.0, 30.0, [{"op": "trim_start", "to": 5.0}, {"op": "trim_end", "to": 25.0}]
    )
    segments, _, _ = resolve_clip_segments(candidate, _cfg())
    assert len(segments) == 1
    assert (segments[0]["start"], segments[0]["end"]) == (5.0, 25.0)


def test_add_lead_in_is_left_unresolved_not_guessed():
    candidate = _candidate("clip-001", "review", 0.0, 30.0, [{"op": "add_lead_in", "needs_prior_range": True}])
    segments, applied, unresolved = resolve_clip_segments(candidate, _cfg())
    assert len(segments) == 1
    assert (segments[0]["start"], segments[0]["end"]) == (0.0, 30.0)
    assert any(op["op"] == "add_lead_in" for op in unresolved)
    assert applied == []


def test_signature_changes_on_reorder_and_range_change():
    base = {
        "segments": [
            {"kind": "source", "source_sha256": "s", "start": 0.0, "end": 5.0, "from_clip_id": "c1"},
            {"kind": "source", "source_sha256": "s", "start": 10.0, "end": 15.0, "from_clip_id": "c2"},
        ]
    }
    reordered = {"segments": [base["segments"][1], base["segments"][0]]}
    assert assembly_signature(base) == assembly_signature(json.loads(json.dumps(base)))
    assert assembly_signature(base) != assembly_signature(reordered)
    changed = json.loads(json.dumps(base))
    changed["segments"][0]["end"] = 6.0
    assert assembly_signature(base) != assembly_signature(changed)


def test_signature_changes_when_bridge_card_text_changes():
    a = {"segments": [{"kind": "card", "card_kind": "bridge", "text": "Six weeks later", "duration": 1.5}]}
    b = {"segments": [{"kind": "card", "card_kind": "bridge", "text": "Later", "duration": 1.5}]}
    assert assembly_signature(a) != assembly_signature(b)


def test_assembly_is_not_renderable_with_a_reject_member():
    assembly = {
        "renderable": True,
        "member_decisions": ["keep", "reject"],
        "segments": [{"kind": "source", "start": 0.0, "end": 5.0}],
    }
    assert not assembly_is_renderable(assembly)


def test_assembly_is_not_renderable_without_source_segments():
    assembly = {
        "renderable": True,
        "member_decisions": ["keep"],
        "segments": [{"kind": "card", "card_kind": "title", "text": "x", "duration": 1.5}],
    }
    assert not assembly_is_renderable(assembly)


def test_build_assemblies_never_includes_reject_clips(tmp_path):
    _write_project(tmp_path)
    out = build_assemblies(tmp_path, _cfg())
    artifact = json.loads(out.read_text(encoding="utf-8"))

    assert artifact["policy"]["reject_never_included"] is True
    assert artifact["policy"]["auto_publish_enabled"] is False
    included_clip_ids = [cid for a in artifact["assemblies"] for cid in a["source_clip_ids"]]
    assert "clip-003" not in included_clip_ids  # the reject clip
    assert len(artifact["assemblies"]) == 2  # keep + trim
    for assembly in artifact["assemblies"]:
        assert "reject" not in assembly["member_decisions"]
        assert assembly["assembly_signature"]


def test_build_assemblies_applies_repair_ops_to_trim_clip(tmp_path):
    _write_project(tmp_path)
    out = build_assemblies(tmp_path, _cfg())
    artifact = json.loads(out.read_text(encoding="utf-8"))
    trim_assembly = next(a for a in artifact["assemblies"] if a["source_clip_ids"] == ["clip-002"])
    source_segment = next(seg for seg in trim_assembly["segments"] if seg["kind"] == "source")
    assert source_segment["end"] == 25.0  # trim_end op applied
    assert any(op["op"] == "trim_end" for op in trim_assembly["applied_ops"])


def test_duration_cap_drops_trailing_segments():
    from video_review_os.assembly import _enforce_duration_cap

    segments = [
        {"kind": "source", "start": 0.0, "end": 6.0},
        {"kind": "source", "start": 10.0, "end": 16.0},
        {"kind": "source", "start": 20.0, "end": 26.0},
    ]
    kept, over_limit, trimmed = _enforce_duration_cap(segments, 7.0)
    assert over_limit is True
    assert len(kept) == 1  # only the first 6s segment fits under the 7s cap
    assert round(trimmed, 1) == 12.0


def test_duration_cap_flags_an_oversized_single_clip(tmp_path):
    from dataclasses import replace

    _write_project(tmp_path)
    config = replace(_cfg(), assembly=replace(_cfg().assembly, max_total_seconds=5.0))
    out = build_assemblies(tmp_path, config)
    artifact = json.loads(out.read_text(encoding="utf-8"))
    # clip-001 is a single 20s keep clip; the cap can't cut mid-content, but it must flag it.
    keep_assembly = next(a for a in artifact["assemblies"] if a["source_clip_ids"] == ["clip-001"])
    assert keep_assembly["over_duration_target"] is True
    assert any(seg["kind"] == "source" for seg in keep_assembly["segments"])


def test_assembly_approval_only_carries_forward_on_signature_match(tmp_path):
    _write_project(tmp_path)
    out = build_assemblies(tmp_path, _cfg())
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assembly = artifact["assemblies"][0]

    approve_assembly(tmp_path, assembly["assembly_id"])
    approvals = load_approvals(tmp_path)
    assert assembly_approval_status(assembly, approvals) == "approved"

    recut = json.loads(json.dumps(assembly))
    recut["segments"][0]["end"] = float(recut["segments"][0]["end"]) + 1.0
    recut.pop("assembly_signature", None)
    assert assembly_approval_status(recut, approvals) == "unreviewed"


def _write_project(project_dir):
    (project_dir / "source.json").write_text(
        json.dumps(
            {
                "project_id": "sample",
                "source": {
                    "sha256": "src",
                    "filename": "sample.mp4",
                    "active_path": str(project_dir / "sample.mp4"),
                },
            }
        ),
        encoding="utf-8",
    )
    candidates = [
        _candidate("clip-001", "keep", 0.0, 20.0),
        _candidate("clip-002", "trim", 0.0, 30.0, [{"op": "trim_end", "to": 25.0, "reason_flag": "weak_ending_word"}]),
        _candidate("clip-003", "reject", 0.0, 5.0),
    ]
    (project_dir / "clips.json").write_text(
        json.dumps({"project_id": "sample", "source_sha256": "src", "candidates": candidates}),
        encoding="utf-8",
    )
