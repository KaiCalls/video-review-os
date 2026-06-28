from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import CopyConfig, VideoReviewConfig
from .quality_gate import score_copy_text
from .utils import atomic_write_json, read_json, utc_now_iso


class CopyProvider:
    def generate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class FallbackCopyProvider(CopyProvider):
    def generate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        text = str(candidate.get("text", "")).strip()
        first_sentence = _first_sentence(text)
        title = _title_from_text(first_sentence or text)
        hook = first_sentence or "Review this clip before posting."
        caption = text if text else "Transcript unavailable. Review the source video before using this draft."
        return {
            "provider": "fallback",
            "status": "fallback",
            "title": title,
            "hook": hook,
            "caption": caption,
            "notes": ["Deterministic fallback copy. Review before use."],
        }


class GenericHttpCopyProvider(CopyProvider):
    def __init__(self, config: CopyConfig) -> None:
        self.config = config

    def generate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        endpoint = os.getenv(self.config.hosted_endpoint_env, "")
        api_key = os.getenv(self.config.hosted_api_key_env, "")
        if not endpoint:
            return FallbackCopyProvider().generate(candidate)
        try:
            import requests  # type: ignore[import-not-found]
        except ImportError:
            return FallbackCopyProvider().generate(candidate)

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        response = requests.post(endpoint, headers=headers, json={"candidate": candidate}, timeout=90)
        response.raise_for_status()
        payload = response.json()
        return {
            "provider": "generic-http",
            "status": "ok",
            "title": str(payload.get("title", "")).strip(),
            "hook": str(payload.get("hook", "")).strip(),
            "caption": str(payload.get("caption", "")).strip(),
            "notes": payload.get("notes", []),
        }


def provider_for(config: CopyConfig) -> CopyProvider:
    if config.provider.strip().lower() == "generic-http":
        return GenericHttpCopyProvider(config)
    return FallbackCopyProvider()


def draft_project_copy(project_dir: Path, config: VideoReviewConfig) -> Path:
    clips = read_json(project_dir / "clips.json")
    provider = provider_for(config.copy)
    drafts = []
    for candidate in clips.get("candidates", []):
        draft = provider.generate(candidate)
        draft["clip_id"] = candidate["clip_id"]
        draft["decision"] = candidate["decision"]
        draft["quality"] = {
            "title": score_copy_text(draft.get("title", ""), "title"),
            "hook": score_copy_text(draft.get("hook", ""), "hook"),
            "caption": score_copy_text(draft.get("caption", ""), "caption"),
        }
        drafts.append(draft)

    artifact = {
        "schema_version": "video_review_os.copy_drafts.v1",
        "created_at": utc_now_iso(),
        "project_id": clips["project_id"],
        "source_sha256": clips["source_sha256"],
        "drafts": drafts,
        "safety": {"auto_publish_enabled": False},
    }
    out = project_dir / "drafts" / "copy.json"
    atomic_write_json(out, artifact)
    return out


def _first_sentence(text: str) -> str:
    for marker in [". ", "? ", "! "]:
        if marker in text:
            return text.split(marker, 1)[0].strip() + marker.strip()
    return text[:160].strip()


def _title_from_text(text: str) -> str:
    words = [word.strip(" ,.!?;:") for word in text.split() if word.strip(" ,.!?;:")]
    if not words:
        return "Review clip"
    title = " ".join(words[:10])
    return title[:80].rstrip(" ,.!?;:")

