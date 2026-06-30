"""Media tagger: write a content tag record for each source video.

The tagger reads ``source.json`` (ffprobe media facts) and ``transcript.json``
(text + words) and emits ``tags.json`` — a source-level record that makes a raw
library findable and feeds a content conveyor: event type, performer, track,
venue, energy, vocal presence, orientation, and a first-class content ``bucket``.

The auto-tags are a deterministic first pass derived from filename, transcript
keywords, and stream geometry. They are meant to be inspected and refined by a
person (or a hosted provider), not treated as ground truth — same contract as the
storyboard and copy layers.

Pluggable providers mirror ``storyboard.py``:

* ``fallback`` — deterministic heuristics, always available, never raises.
* ``generic-http`` — POST source + transcript to your own endpoint; the response
  is sanitized so it can only return known buckets/orientations. Falls back
  deterministically on any failure.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .config import TaggingConfig, VideoReviewConfig
from .utils import atomic_write_json, read_json, utc_now_iso

ORIENTATIONS = ("vertical", "horizontal", "square", "unknown")

# Coarse keyword signals. Each maps a tag value to the words that vote for it; the
# value with the most votes across filename + transcript wins, ties broken by order.
EVENT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "wedding": ("wedding", "bride", "groom", "ceremony", "reception", "first dance", "aisle", "vows", "bridal"),
    "gala": ("gala", "fundraiser", "benefit", "nonprofit", "auction"),
    "corporate": ("corporate", "company", "conference", "keynote", "holiday party", "summit"),
}
ENERGY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "peak": ("party", "dance floor", "celebration", "encore", "hands up", "turn up", "let's go"),
    "low": ("ceremony", "vows", "first dance", "speech", "toast", "cocktail", "acoustic"),
}
# Second-person address that suggests a talking-to-camera clip rather than performance.
DIRECT_ADDRESS = ("you", "your", "you're", "here's how", "if you", "let me show", "book", "question")


def evaluate_source(
    source: dict[str, Any],
    transcript: dict[str, Any],
    config: TaggingConfig | None = None,
) -> dict[str, Any]:
    """Deterministically derive a tag record from media + transcript facts."""
    tag_cfg = config or TaggingConfig()
    media = source.get("media", {}) or {}
    video = media.get("video", {}) or {}
    audio = media.get("audio", {}) or {}

    filename = str(source.get("source", {}).get("filename", ""))
    text = str(transcript.get("text", "")).strip()
    if not text:
        text = " ".join(str(w.get("word", "")) for w in transcript.get("words", [])).strip()
    haystack = f"{filename} {text}".lower()

    width = _int(video.get("width"))
    height = _int(video.get("height"))
    orientation = _orientation(width, height)
    duration = _float(media.get("duration_seconds"))
    has_audio = bool(audio.get("codec"))
    has_transcript = bool(text)
    has_vocal = has_audio and has_transcript

    performer = _first_match(haystack, tag_cfg.roster)
    venue = _first_match(haystack, tag_cfg.venues)
    event_type = _best_keyword(haystack, EVENT_TYPE_KEYWORDS)
    energy = _best_keyword(haystack, ENERGY_KEYWORDS) or "build"
    usable_vertical = orientation in {"vertical", "square"}

    flags: list[str] = []
    if not has_audio:
        flags.append("no_audio_stream")
    if not has_transcript:
        flags.append("no_transcript")
    if orientation == "horizontal":
        flags.append("horizontal_needs_reframe")
    if orientation == "unknown":
        flags.append("unknown_orientation")
    if duration is not None and duration < 3.0:
        flags.append("very_short")

    bucket = _bucket(
        tag_cfg,
        has_vocal=has_vocal,
        performer=performer,
        event_type=event_type,
        haystack=haystack,
    )

    return {
        "clip_id": str(source.get("source", {}).get("sha256", ""))[:12],
        "source": str(source.get("source", {}).get("original_path", "")),
        "event_type": event_type,
        "performer": performer,
        "track": None,
        "venue": venue,
        "energy": energy,
        "has_vocal": has_vocal,
        "vocal_performer": performer if has_vocal else None,
        "orientation": orientation,
        "usable_vertical": usable_vertical,
        "duration": round(duration, 3) if duration is not None else None,
        "bucket": bucket,
        "quality_flags": flags,
        "tagged_at": utc_now_iso(),
    }


def _orientation(width: int | None, height: int | None) -> str:
    if not width or not height:
        return "unknown"
    if height > width:
        return "vertical"
    if width > height:
        return "horizontal"
    return "square"


def _bucket(
    config: TaggingConfig,
    *,
    has_vocal: bool,
    performer: str | None,
    event_type: str | None,
    haystack: str,
) -> str:
    """Assign a first-class content bucket (Component 3 taxonomy).

    real_gfa    = identified performer performing at a real event (the high-craft lane).
    direct_camera = talking-to-camera, even if short.
    default     = b-roll + trending audio (config.default_bucket).
    """
    if has_vocal and performer and event_type:
        candidate = "real_gfa"
    elif has_vocal and any(cue in haystack for cue in DIRECT_ADDRESS):
        candidate = "direct_camera"
    else:
        candidate = config.default_bucket
    return candidate if candidate in config.buckets else config.default_bucket


def _best_keyword(haystack: str, keyword_map: dict[str, tuple[str, ...]]) -> str | None:
    best: str | None = None
    best_votes = 0
    for value, words in keyword_map.items():
        votes = sum(haystack.count(word) for word in words)
        if votes > best_votes:
            best = value
            best_votes = votes
    return best


def _first_match(haystack: str, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        name = candidate.strip()
        if name and re.search(rf"\b{re.escape(name.lower())}\b", haystack):
            return name
    return None


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class TagProvider:
    def tag(self, source: dict[str, Any], transcript: dict[str, Any], config: VideoReviewConfig) -> dict[str, Any]:
        raise NotImplementedError


class FallbackTagProvider(TagProvider):
    def tag(self, source: dict[str, Any], transcript: dict[str, Any], config: VideoReviewConfig) -> dict[str, Any]:
        return {
            "provider": "fallback",
            "status": "ok",
            "tags": evaluate_source(source, transcript, config.tagging),
        }


class GenericHttpTagProvider(TagProvider):
    def __init__(self, config: TaggingConfig) -> None:
        self.config = config

    def tag(self, source: dict[str, Any], transcript: dict[str, Any], config: VideoReviewConfig) -> dict[str, Any]:
        endpoint = os.getenv(self.config.hosted_endpoint_env, "")
        api_key = os.getenv(self.config.hosted_api_key_env, "")
        deterministic = evaluate_source(source, transcript, config.tagging)
        if not endpoint:
            return {"provider": "fallback", "status": "ok", "tags": deterministic}
        try:
            import requests  # type: ignore[import-not-found]
        except ImportError:
            return {"provider": "fallback", "status": "ok", "tags": deterministic}

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json={
                    "source": source,
                    "transcript": transcript,
                    "buckets": list(config.tagging.buckets),
                    "fallback_tags": deterministic,
                },
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001 - any provider failure must degrade, never abort.
            return {
                "provider": "fallback",
                "status": "fallback",
                "errors": ["Tag provider failed; used deterministic fallback."],
                "tags": deterministic,
            }
        return {
            "provider": "generic-http",
            "status": "ok",
            "tags": _sanitize_tags(payload.get("tags", {}), deterministic, config.tagging),
        }


def provider_for(config: TaggingConfig) -> TagProvider:
    if config.provider.strip().lower() == "generic-http":
        return GenericHttpTagProvider(config)
    return FallbackTagProvider()


def _sanitize_tags(raw: Any, deterministic: dict[str, Any], config: TaggingConfig) -> dict[str, Any]:
    """Constrain a hosted response to the known schema so a provider cannot invent
    enum values; unknown bucket/orientation fall back to the deterministic value."""
    if not isinstance(raw, dict):
        return deterministic
    tags = dict(deterministic)
    for key in ("event_type", "performer", "track", "venue", "energy", "vocal_performer"):
        if key in raw:
            tags[key] = raw[key]
    if "has_vocal" in raw:
        tags["has_vocal"] = bool(raw["has_vocal"])
    if "usable_vertical" in raw:
        tags["usable_vertical"] = bool(raw["usable_vertical"])
    if raw.get("orientation") in ORIENTATIONS:
        tags["orientation"] = raw["orientation"]
    if raw.get("bucket") in config.buckets:
        tags["bucket"] = raw["bucket"]
    if isinstance(raw.get("quality_flags"), list):
        tags["quality_flags"] = [str(flag) for flag in raw["quality_flags"]]
    return tags


def tag_project(project_dir: Path, config: VideoReviewConfig) -> Path:
    source = read_json(project_dir / "source.json")
    transcript_path = project_dir / "transcript.json"
    transcript = read_json(transcript_path) if transcript_path.exists() else {}
    provider = provider_for(config.tagging)
    result = provider.tag(source, transcript, config)
    artifact = {
        "schema_version": "video_review_os.tags.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "provider": result.get("provider", "fallback"),
        "status": result.get("status", "ok"),
        "buckets": list(config.tagging.buckets),
        "tags": result.get("tags", {}),
    }
    if result.get("errors"):
        artifact["errors"] = result["errors"]
    out = project_dir / "tags.json"
    atomic_write_json(out, artifact)
    return out
