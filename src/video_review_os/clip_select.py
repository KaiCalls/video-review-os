from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import VideoReviewConfig
from .quality_gate import evaluate_candidate
from .utils import atomic_write_json, read_json, sha256_text, utc_now_iso


def select_project_clips(project_dir: Path, config: VideoReviewConfig) -> Path:
    source = read_json(project_dir / "source.json")
    transcript_path = project_dir / "transcript.json"
    transcript = read_json(transcript_path) if transcript_path.exists() else {}
    candidates = build_candidates(source, transcript, config)
    artifact = {
        "schema_version": "video_review_os.clips.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "candidates": candidates,
        "selection_policy": {
            "default_render_decisions": list(config.render.default_decisions),
            "reject_never_renders": True,
            "auto_publish_enabled": False,
        },
    }
    out = project_dir / "clips.json"
    atomic_write_json(out, artifact)
    return out


def build_candidates(
    source: dict[str, Any],
    transcript: dict[str, Any],
    config: VideoReviewConfig,
) -> list[dict[str, Any]]:
    words = _normalized_words(transcript.get("words", []))
    media_duration = source.get("media", {}).get("duration_seconds")
    if words:
        ranges = _ranges_from_words(words, config)
    else:
        fallback_end = min(float(media_duration or 30.0), 30.0)
        ranges = [{"start": 0.0, "end": fallback_end, "words": [], "text": ""}]

    candidates = []
    for idx, item in enumerate(ranges, start=1):
        text = item.get("text") or " ".join(word["word"] for word in item.get("words", [])).strip()
        candidate = {
            "clip_id": f"clip-{idx:03d}",
            "project_id": source["project_id"],
            "source_sha256": source["source"]["sha256"],
            "start": round(float(item["start"]), 3),
            "end": round(float(item["end"]), 3),
            "text": text,
            "text_sha256": sha256_text(text),
            "words": item.get("words", []),
            "media_duration_seconds": media_duration,
        }
        candidate["quality_gate"] = evaluate_candidate(candidate, config.gate)
        candidate["decision"] = candidate["quality_gate"]["decision"]
        candidates.append(candidate)
    return candidates


def _ranges_from_words(words: list[dict[str, Any]], config: VideoReviewConfig) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        ranges.append(
            {
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "words": current,
                "text": " ".join(word["word"] for word in current).strip(),
            }
        )
        current = []

    for word in words:
        if not current:
            current.append(word)
            continue
        gap = word["start"] - current[-1]["end"]
        duration = word["end"] - current[0]["start"]
        if gap > config.gate.awkward_pause_seconds and duration >= config.gate.min_clip_seconds:
            flush()
        elif duration >= config.gate.max_clip_seconds:
            flush()
        current.append(word)
    flush()
    return ranges


def _normalized_words(raw_words: Any) -> list[dict[str, Any]]:
    words = []
    for item in raw_words or []:
        word = str(item.get("word", "")).strip()
        if not word:
            continue
        try:
            words.append(
                {
                    "word": word,
                    "start": float(item["start"]),
                    "end": float(item["end"]),
                    "confidence": item.get("confidence"),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(words, key=lambda item: item["start"])

