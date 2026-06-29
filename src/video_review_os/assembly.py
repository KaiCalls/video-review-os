"""Assembly layer: turn scored clip candidates into finished multi-range edit drafts.

An *assembly* is the renderable unit of this pipeline. It is an ordered list of
segments, where each segment is either a ``source`` range (a cut from the original
video) or a ``card`` (a generated title/bridge/context slate). A plain single-range
clip is just a one-segment assembly, so the legacy path is a degenerate case of this
one.

Hard invariants enforced here:

* ``reject`` clips are never included as a source segment, in any assembly.
* Nothing is published; assemblies are review-only drafts.
* The assembly signature covers the entire ordered segment list, so approval cannot
  carry forward onto a re-cut, reordered, or re-bridged edit.
* Repair ops with exact timings are applied deterministically; suggestions that need
  an adjacent range (``add_lead_in``) are recorded as unresolved, never guessed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import VideoReviewConfig
from .silence import load_silence_intervals
from .utils import atomic_write_json, ensure_dir, read_json, sha256_text, utc_now_iso
from .visuals import card_svg


def assembly_signature(assembly: dict[str, Any]) -> str:
    """Stable signature over the ordered segment list.

    Reordering segments, changing any range, or editing a bridge-card's text changes
    the signature, which invalidates any prior approval (review-only safety).
    """
    parts: list[str] = []
    for segment in assembly.get("segments", []):
        if segment.get("kind") == "source":
            # Sign on CONTENT (source hash + range), not on the internal clip id — re-running
            # clip selection can renumber clips without changing the actual footage.
            parts.append(
                "S|{sha}|{start:.3f}|{end:.3f}".format(
                    sha=segment.get("source_sha256", ""),
                    start=float(segment.get("start", 0.0)),
                    end=float(segment.get("end", 0.0)),
                )
            )
        else:
            parts.append(
                "C|{kind}|{text}|{dur:.3f}".format(
                    kind=segment.get("card_kind", ""),
                    text=sha256_text(str(segment.get("text", ""))),
                    dur=float(segment.get("duration", 0.0)),
                )
            )
    return sha256_text("||".join(parts))


def resolve_clip_segments(
    candidate: dict[str, Any],
    config: VideoReviewConfig,
    silence_intervals: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply a clip's repair ops (and detected audio silence) to its source range.

    Returns ``(source_segments, applied_ops, unresolved_ops)``. ``drop_span`` ops and
    overlapping detected-silence spans split one range into several; ``trim_start``/
    ``trim_end`` shrink it; ``add_lead_in`` is left unresolved because it needs a
    neighbouring range.
    """
    start = float(candidate.get("start", 0.0))
    end = float(candidate.get("end", 0.0))
    source_sha256 = candidate.get("source_sha256", "")
    clip_id = candidate.get("clip_id", "")
    ops = []
    if config.assembly.apply_repair_ops:
        ops = list(candidate.get("quality_gate", {}).get("repair_ops", []))

    applied: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    trim_start_to = start
    trim_end_to = end
    drop_spans: list[tuple[float, float]] = []
    for interval in silence_intervals or []:
        span_start = max(start, float(interval.get("start", 0.0)))
        span_end = min(end, float(interval.get("end", 0.0)))
        if span_end - span_start > 0.05:
            drop_spans.append((span_start, span_end))
            applied.append(
                {
                    "op": "drop_span",
                    "start": round(span_start, 3),
                    "end": round(span_end, 3),
                    "reason_flag": "audio_silence",
                    "source": "silencedetect",
                }
            )
    for op in ops:
        kind = op.get("op")
        if kind == "trim_start":
            to = float(op.get("to", start))
            if start < to < end:
                trim_start_to = max(trim_start_to, to)
                applied.append(op)
        elif kind == "trim_end":
            to = float(op.get("to", end))
            if start < to < end:
                trim_end_to = min(trim_end_to, to)
                applied.append(op)
        elif kind == "drop_span":
            span_start = float(op.get("start", 0.0))
            span_end = float(op.get("end", 0.0))
            if span_end > span_start:
                drop_spans.append((span_start, span_end))
                applied.append(op)
        else:
            unresolved.append(op)

    base_start = trim_start_to
    base_end = trim_end_to
    if base_end <= base_start:
        # Repair ops collapsed the range; fall back to the original, untrimmed clip.
        base_start, base_end = start, end
        applied = [op for op in applied if op.get("op") == "add_lead_in"]
        unresolved = [op for op in ops if op not in applied]
        drop_spans = []

    intervals = _subtract_spans(base_start, base_end, drop_spans)
    min_len = config.assembly.min_segment_seconds
    segments: list[dict[str, Any]] = []
    for seg_start, seg_end in intervals:
        if seg_end - seg_start < min_len:
            continue
        segments.append(
            {
                "kind": "source",
                "source_sha256": source_sha256,
                "start": round(seg_start, 3),
                "end": round(seg_end, 3),
                "from_clip_id": clip_id,
            }
        )
    if not segments:
        # Never drop a clip to zero segments from repair ops alone; keep the raw range.
        segments.append(
            {
                "kind": "source",
                "source_sha256": source_sha256,
                "start": round(start, 3),
                "end": round(end, 3),
                "from_clip_id": clip_id,
            }
        )
        applied = [op for op in applied if op.get("op") == "add_lead_in"]
    return segments, applied, unresolved


def _subtract_spans(start: float, end: float, spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    intervals = [(start, end)]
    for span_start, span_end in sorted(spans):
        next_intervals: list[tuple[float, float]] = []
        for cur_start, cur_end in intervals:
            if span_end <= cur_start or span_start >= cur_end:
                next_intervals.append((cur_start, cur_end))
                continue
            if span_start > cur_start:
                next_intervals.append((cur_start, span_start))
            if span_end < cur_end:
                next_intervals.append((span_end, cur_end))
        intervals = next_intervals
    return intervals


def build_assemblies(project_dir: Path, config: VideoReviewConfig) -> Path:
    source = read_json(project_dir / "source.json")
    clips = read_json(project_dir / "clips.json")
    candidates_by_id = {c["clip_id"]: c for c in clips.get("candidates", [])}

    storyboard_path = project_dir / "storyboard.json"
    if storyboard_path.exists():
        storyboard = read_json(storyboard_path)
    else:
        # Deterministic fallback so `assemble` runs standalone without a storyboard pass.
        from .storyboard import fallback_storyboard

        storyboard = fallback_storyboard(clips, config)

    silence_intervals = load_silence_intervals(project_dir)
    cards_dir = project_dir / "assemblies" / "cards"
    assemblies: list[dict[str, Any]] = []
    for entry in storyboard.get("assemblies", []):
        assembly = _resolve_assembly(entry, candidates_by_id, config, cards_dir, silence_intervals)
        if assembly is not None:
            assemblies.append(assembly)

    artifact = {
        "schema_version": "video_review_os.assemblies.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "storyboard_provider": storyboard.get("provider", "fallback"),
        "policy": {
            "reject_never_included": True,
            "auto_publish_enabled": False,
            "review_only": True,
        },
        "assemblies": assemblies,
    }
    out = project_dir / "assemblies.json"
    atomic_write_json(out, artifact)
    return out


def _resolve_assembly(
    entry: dict[str, Any],
    candidates_by_id: dict[str, dict[str, Any]],
    config: VideoReviewConfig,
    cards_dir: Path,
    silence_intervals: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    assembly_id = entry.get("assembly_id", "asm-000")
    ordering = entry.get("ordering") or entry.get("member_clip_ids") or []
    bridge_cards = {card.get("after_clip_id"): card for card in entry.get("bridge_cards", [])}

    # Reject clips are never included as source. Drop unknown/rejected members up front.
    members = [
        candidates_by_id[clip_id]
        for clip_id in ordering
        if clip_id in candidates_by_id and str(candidates_by_id[clip_id].get("decision", "")).lower() != "reject"
    ]
    dropped_reject = [
        clip_id
        for clip_id in ordering
        if clip_id in candidates_by_id and str(candidates_by_id[clip_id].get("decision", "")).lower() == "reject"
    ]
    if not members:
        return None

    segments: list[dict[str, Any]] = []
    applied_ops: list[dict[str, Any]] = []
    unresolved_ops: list[dict[str, Any]] = []
    member_decisions: list[str] = []
    card_index = 0

    # Hook-first: a title card cold-opens before the content only if explicitly configured;
    # the default is "tail" so the strongest moment leads (short-form retention).
    title_card = entry.get("title_card")
    title_seg = None
    if title_card and str(title_card.get("text", "")).strip() and config.assembly.title_card_position != "none":
        title_seg = _make_card(title_card, "title", config, cards_dir, assembly_id, card_index)
        card_index += 1
    if title_seg and config.assembly.title_card_position == "head":
        segments.append(title_seg)

    for candidate in members:
        member_decisions.append(str(candidate.get("decision", "")))
        clip_segments, applied, unresolved = resolve_clip_segments(candidate, config, silence_intervals)
        segments.extend(clip_segments)
        applied_ops.extend(applied)
        unresolved_ops.extend(unresolved)
        bridge = bridge_cards.get(candidate.get("clip_id"))
        if bridge and str(bridge.get("text", "")).strip():
            segments.append(
                _make_card(bridge, bridge.get("kind", "bridge"), config, cards_dir, assembly_id, card_index)
            )
            card_index += 1

    if title_seg and config.assembly.title_card_position == "tail":
        segments.append(title_seg)

    if len(segments) > config.assembly.max_segments:
        segments = segments[: config.assembly.max_segments]

    segments, over_limit, trimmed_seconds = _enforce_duration_cap(segments, config.assembly.max_total_seconds)

    source_segments = [seg for seg in segments if seg.get("kind") == "source"]
    if not source_segments:
        return None

    total_duration = sum(
        (float(seg["end"]) - float(seg["start"])) if seg["kind"] == "source" else float(seg.get("duration", 0.0))
        for seg in segments
    )
    source_clip_ids = [c["clip_id"] for c in members]
    assembly = {
        "assembly_id": assembly_id,
        "kind": "single" if len(members) == 1 else "multi",
        "rationale": entry.get("rationale", ""),
        "source_clip_ids": source_clip_ids,
        "member_decisions": member_decisions,
        "dropped_reject_clip_ids": dropped_reject,
        "segments": segments,
        "applied_ops": applied_ops,
        "unresolved_ops": unresolved_ops,
        "total_duration": round(total_duration, 3),
        "over_duration_target": over_limit,
        "trimmed_seconds": round(trimmed_seconds, 3),
        "renderable": True,
        "auto_publish_enabled": False,
    }
    assembly["assembly_signature"] = assembly_signature(assembly)
    return assembly


def _enforce_duration_cap(
    segments: list[dict[str, Any]],
    max_total_seconds: float,
) -> tuple[list[dict[str, Any]], bool, float]:
    """Keep assemblies within a platform target by dropping trailing segments (never silently:
    the caller records ``over_duration_target`` and how many seconds were trimmed)."""

    def seg_seconds(seg: dict[str, Any]) -> float:
        if seg.get("kind") == "source":
            return float(seg["end"]) - float(seg["start"])
        return float(seg.get("duration", 0.0))

    total = sum(seg_seconds(seg) for seg in segments)
    if max_total_seconds <= 0 or total <= max_total_seconds:
        return segments, False, 0.0

    kept: list[dict[str, Any]] = []
    running = 0.0
    for seg in segments:
        seconds = seg_seconds(seg)
        if running + seconds > max_total_seconds:
            break
        kept.append(seg)
        running += seconds
    # Guard: keep at least one source segment even if the first segment already exceeds the cap.
    if not any(s.get("kind") == "source" for s in kept):
        first_source = next((s for s in segments if s.get("kind") == "source"), None)
        if first_source is not None:
            kept = [first_source]
            running = seg_seconds(first_source)
    return kept, True, total - running


def _make_card(
    card: dict[str, Any],
    card_kind: str,
    config: VideoReviewConfig,
    cards_dir: Path,
    assembly_id: str,
    index: int,
) -> dict[str, Any]:
    text = str(card.get("text", "")).strip()
    duration = float(card.get("duration", config.assembly.card_seconds))
    ensure_dir(cards_dir)
    svg_path = cards_dir / f"{assembly_id}-card-{index:02d}.svg"
    svg_path.write_text(card_svg(text, card_kind, config), encoding="utf-8")
    return {
        "kind": "card",
        "card_kind": card_kind,
        "text": text,
        "duration": round(duration, 3),
        "svg_path": str(svg_path),
    }


def assembly_is_renderable(assembly: dict[str, Any]) -> bool:
    """Defense in depth: a reject member or empty source list is never renderable."""
    if not assembly.get("renderable", False):
        return False
    if any(str(decision).lower() == "reject" for decision in assembly.get("member_decisions", [])):
        return False
    return any(seg.get("kind") == "source" for seg in assembly.get("segments", []))
