from __future__ import annotations

import re
from typing import Any

from .config import GateConfig
from .utils import clamp

FILLERS = {"um", "uh", "erm", "ah", "like", "okay", "so"}
CONTEXT_STARTS = {
    "and",
    "but",
    "so",
    "because",
    "which",
    "that",
    "this",
    "these",
    "those",
    "it",
    "they",
    "he",
    "she",
    "we",
    "also",
    "then",
    "or",
    "yeah",
    "right",
}
WEAK_ENDINGS = {"and", "but", "so", "because", "like", "um", "uh", "or", "the", "a", "to", "of"}
LOW_VALUE_HOOKS = {
    "check this out",
    "you need to hear this",
    "this is crazy",
    "this changed everything",
    "you will not believe",
    "here is why",
}
SLATE_RE = re.compile(
    r"\b(cut|take|slate)\s+(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b",
    re.IGNORECASE,
)
REPEATED_NONSENSE_RE = re.compile(r"\b(test|blah|asdf|nonsense)\b(?:\s+\1\b)+", re.IGNORECASE)


def evaluate_candidate(candidate: dict[str, Any], config: GateConfig | None = None) -> dict[str, Any]:
    gate = config or GateConfig()
    text = str(candidate.get("text", "")).strip()
    start = float(candidate.get("start", 0.0))
    end = float(candidate.get("end", 0.0))
    duration = max(0.0, end - start)
    words = _word_items(candidate.get("words", []))
    tokens = _tokens(text)
    opening = tokens[: gate.opening_word_window]
    score = 100
    flags: list[dict[str, Any]] = []

    def add_flag(flag_id: str, message: str, deduction: int, severity: str = "medium") -> None:
        nonlocal score
        flags.append(
            {
                "id": flag_id,
                "severity": severity,
                "deduction": deduction,
                "message": message,
            }
        )
        score -= deduction

    if not text:
        add_flag("missing_transcript", "No transcript text is available for this range.", 30, "high")

    if duration < gate.min_clip_seconds:
        add_flag(
            "too_short_standalone_range",
            f"Clip is {duration:.1f}s; standalone minimum is {gate.min_clip_seconds:.1f}s.",
            35,
            "high",
        )
    elif duration < gate.ideal_min_seconds:
        add_flag(
            "short_standalone_range",
            f"Clip is {duration:.1f}s; review whether it has enough context.",
            10,
            "medium",
        )

    if duration > gate.max_clip_seconds:
        add_flag(
            "too_long_for_short_draft",
            f"Clip is {duration:.1f}s; default short draft maximum is {gate.max_clip_seconds:.1f}s.",
            10,
            "low",
        )

    media_duration = candidate.get("media_duration_seconds")
    if media_duration is not None and end > float(media_duration) + 0.25:
        add_flag("incomplete_video_segment", "Clip end exceeds the inspected media duration.", 45, "high")

    first = opening[0] if opening else ""
    if first in CONTEXT_STARTS:
        add_flag(
            "starts_mid_thought",
            f"Opening word '{first}' often depends on missing prior context.",
            18,
            "medium",
        )

    if first in {"this", "that", "it", "they", "these", "those"}:
        add_flag(
            "missing_context_opening",
            "Opening uses a referent before the viewer knows what it refers to.",
            16,
            "medium",
        )

    filler_count = sum(1 for token in opening if token in FILLERS)
    if opening and (opening[0] in FILLERS or filler_count >= 2):
        add_flag(
            "filler_heavy_opening",
            "Opening contains filler before the point is clear.",
            18,
            "medium",
        )

    repeated = _repeated_adjacent(tokens[:12])
    if repeated:
        add_flag(
            "stammer_or_restart",
            f"Opening repeats '{repeated}' in a way that may sound like a restart.",
            16,
            "medium",
        )

    if SLATE_RE.search(text) or REPEATED_NONSENSE_RE.search(text):
        add_flag(
            "slate_or_outtake_marker",
            "Transcript contains slate, outtake, or repeated placeholder language.",
            50,
            "high",
        )

    max_pause = _max_word_gap(words)
    if max_pause > gate.awkward_pause_seconds:
        add_flag(
            "awkward_pause",
            f"Longest word gap is {max_pause:.1f}s; review the audio pacing.",
            15,
            "medium",
        )

    if tokens and tokens[-1] in WEAK_ENDINGS:
        add_flag(
            "weak_ending_word",
            f"Clip ends on '{tokens[-1]}', which may feel unfinished.",
            14,
            "medium",
        )

    score = int(clamp(score, 0, 100))
    fatal = {flag["id"] for flag in flags if flag["severity"] == "high"}
    flag_ids = {flag["id"] for flag in flags}
    needs_trim = bool(
        {
            "starts_mid_thought",
            "missing_context_opening",
            "filler_heavy_opening",
            "stammer_or_restart",
            "awkward_pause",
            "weak_ending_word",
        }
        & flag_ids
    )

    if {"slate_or_outtake_marker", "incomplete_video_segment", "too_short_standalone_range"} & fatal:
        decision = "reject"
    elif score >= gate.keep_threshold and not fatal and not needs_trim:
        decision = "keep"
    elif score >= gate.trim_threshold:
        decision = "trim"
    elif score >= gate.review_threshold:
        decision = "review"
    else:
        decision = "reject"

    repair_words = _repair_words(candidate)
    repair_ops = _build_repair_ops(repair_words, flag_ids, start, end, gate)

    return {
        "schema_version": "video_review_os.quality_gate.v2",
        "score": score,
        "decision": decision,
        "render_allowed_default": decision == "keep",
        "repair_ops": repair_ops,
        "flags": flags,
        "metrics": {
            "duration_seconds": round(duration, 3),
            "word_count": len(tokens),
            "opening_filler_count": filler_count,
            "max_word_gap_seconds": round(max_pause, 3),
        },
    }


def score_copy_text(value: str, field: str = "copy") -> dict[str, Any]:
    text = value.strip()
    lower = text.lower()
    flags: list[dict[str, str]] = []
    if not text:
        flags.append({"id": "empty_copy", "message": f"{field} is empty."})
    if any(hook in lower for hook in LOW_VALUE_HOOKS):
        flags.append({"id": "generic_low_value_hook", "message": f"{field} uses generic hook language."})
    if len(_tokens(text)) < 4:
        flags.append({"id": "too_short_copy", "message": f"{field} may not carry enough value."})
    return {"ok": not flags, "flags": flags}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9']+", text.lower())


def _repeated_adjacent(tokens: list[str]) -> str | None:
    previous = None
    for token in tokens:
        if token == previous and token not in {"a", "the"}:
            return token
        previous = token
    for i in range(0, max(0, len(tokens) - 3)):
        if tokens[i : i + 2] == tokens[i + 2 : i + 4]:
            return " ".join(tokens[i : i + 2])
    return None


def _word_items(raw_words: Any) -> list[dict[str, float]]:
    words = []
    for item in raw_words or []:
        try:
            words.append({"start": float(item["start"]), "end": float(item["end"])})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(words, key=lambda word: word["start"])


def _max_word_gap(words: list[dict[str, float]]) -> float:
    if len(words) < 2:
        return 0.0
    return max(max(0.0, right["start"] - left["end"]) for left, right in zip(words, words[1:]))


def _repair_words(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Word list that preserves the token text (unlike `_word_items`) for trim targeting."""
    out: list[dict[str, Any]] = []
    for item in candidate.get("words", []) or []:
        try:
            word = str(item.get("word", "")).strip()
            if not word:
                continue
            out.append({"word": word, "start": float(item["start"]), "end": float(item["end"])})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda entry: entry["start"])


def _norm_word(word: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", word.lower())


def _max_word_gap_bounds(words: list[dict[str, Any]]) -> tuple[float, float, float]:
    """Return (gap_seconds, span_start, span_end) for the longest inter-word silence."""
    best = (0.0, 0.0, 0.0)
    for left, right in zip(words, words[1:]):
        gap = max(0.0, right["start"] - left["end"])
        if gap > best[0]:
            best = (gap, left["end"], right["start"])
    return best


def _build_repair_ops(
    words: list[dict[str, Any]],
    flag_ids: set[str],
    start: float,
    end: float,
    gate: GateConfig,
) -> list[dict[str, Any]]:
    """Translate diagnostic flags into actionable, deterministic edit instructions.

    Ops with an exact `to`/`start`/`end` are resolvable by the assembly layer without
    a human. `add_lead_in` is a suggestion that needs an adjacent range, so it is left
    for the storyboard layer (or a person) to resolve.
    """
    ops: list[dict[str, Any]] = []

    trim_start_to: float | None = None
    trim_reason = ""
    if "filler_heavy_opening" in flag_ids and words:
        idx = 0
        while idx < len(words) and _norm_word(words[idx]["word"]) in FILLERS:
            idx += 1
        if 0 < idx < len(words):
            trim_start_to = words[idx]["start"]
            trim_reason = "filler_heavy_opening"
    if "stammer_or_restart" in flag_ids and len(words) >= 3:
        for i in range(len(words) - 1):
            left = _norm_word(words[i]["word"])
            right = _norm_word(words[i + 1]["word"])
            if left and left == right and left not in {"a", "the"} and i + 2 < len(words):
                candidate_to = words[i + 2]["start"]
                if trim_start_to is None or candidate_to > trim_start_to:
                    trim_start_to = candidate_to
                    trim_reason = "stammer_or_restart"
                break
    if trim_start_to is not None and trim_start_to > start + 0.05:
        ops.append(
            {
                "op": "trim_start",
                "to": round(trim_start_to, 3),
                "reason_flag": trim_reason,
                "confidence": 0.7,
            }
        )

    if "weak_ending_word" in flag_ids and len(words) >= 2:
        cut_to = words[-2]["end"]
        if start < cut_to < end - 0.05:
            ops.append(
                {
                    "op": "trim_end",
                    "to": round(cut_to, 3),
                    "reason_flag": "weak_ending_word",
                    "confidence": 0.6,
                }
            )

    if "awkward_pause" in flag_ids:
        gap, span_start, span_end = _max_word_gap_bounds(words)
        if gap > gate.awkward_pause_seconds and span_end > span_start:
            ops.append(
                {
                    "op": "drop_span",
                    "start": round(span_start, 3),
                    "end": round(span_end, 3),
                    "reason_flag": "awkward_pause",
                    "confidence": 0.85,
                }
            )

    if {"starts_mid_thought", "missing_context_opening"} & flag_ids:
        ops.append(
            {
                "op": "add_lead_in",
                "needs_prior_range": True,
                "note": "Opening depends on earlier context; combine with the preceding moment or add a context card.",
                "reason_flag": "starts_mid_thought" if "starts_mid_thought" in flag_ids else "missing_context_opening",
                "confidence": 0.4,
            }
        )

    return ops
