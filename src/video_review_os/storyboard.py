"""Storyboard layer: propose how candidate clips become finished assemblies.

A storyboard is a *proposal* only — it never edits or publishes. Each proposed
assembly names its member clips, their order, optional bridge/title cards, and a
rationale. The assembly layer resolves the proposal into a concrete EDL.

Pluggable providers mirror ``copy.py``:

* ``fallback`` — deterministic: one assembly per non-reject candidate, no combining.
  Always available, never raises.
* ``generic-http`` — POST the clips artifact to your own LLM endpoint; the endpoint may
  combine, reorder, and add cards. Falls back deterministically on any failure.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import StoryboardConfig, VideoReviewConfig
from .utils import atomic_write_json, read_json, utc_now_iso


class StoryboardProvider:
    def propose(self, clips: dict[str, Any], config: VideoReviewConfig) -> dict[str, Any]:
        raise NotImplementedError


class FallbackStoryboardProvider(StoryboardProvider):
    def propose(self, clips: dict[str, Any], config: VideoReviewConfig) -> dict[str, Any]:
        return fallback_storyboard(clips, config)


class GenericHttpStoryboardProvider(StoryboardProvider):
    def __init__(self, config: StoryboardConfig) -> None:
        self.config = config

    def propose(self, clips: dict[str, Any], config: VideoReviewConfig) -> dict[str, Any]:
        endpoint = os.getenv(self.config.hosted_endpoint_env, "")
        api_key = os.getenv(self.config.hosted_api_key_env, "")
        if not endpoint:
            return fallback_storyboard(clips, config)
        try:
            import requests  # type: ignore[import-not-found]
        except ImportError:
            return fallback_storyboard(clips, config)

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json={"clips": clips, "include_decisions": list(config.assembly.include_decisions)},
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001 - any provider failure must degrade, never abort.
            fallback = fallback_storyboard(clips, config)
            fallback["status"] = "fallback"
            fallback["errors"] = ["Storyboard provider failed; used deterministic fallback."]
            return fallback

        assemblies = _sanitize_assemblies(payload.get("assemblies", []), clips, config)
        if not assemblies:
            return fallback_storyboard(clips, config)
        return {
            "schema_version": "video_review_os.storyboard.v1",
            "provider": "generic-http",
            "status": "ok",
            "assemblies": assemblies,
        }


def provider_for(config: StoryboardConfig) -> StoryboardProvider:
    if config.provider.strip().lower() == "generic-http":
        return GenericHttpStoryboardProvider(config)
    return FallbackStoryboardProvider()


def fallback_storyboard(clips: dict[str, Any], config: VideoReviewConfig) -> dict[str, Any]:
    include = set(config.assembly.include_decisions)
    eligible = [
        candidate
        for candidate in clips.get("candidates", [])
        if str(candidate.get("decision", "")).lower() != "reject"
        and str(candidate.get("decision", "")).lower() in include
    ]
    groups = _combine_candidates(eligible, config)

    assemblies: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        clip_ids = [candidate["clip_id"] for candidate in group]
        if len(group) > 1:
            rationale = "Combined adjacent clips so a mid-thought opener keeps its lead-in context."
        else:
            ops = group[0].get("quality_gate", {}).get("repair_ops", [])
            rationale = (
                "Single-clip draft with deterministic repair ops applied."
                if ops
                else "Single-clip draft; clip passed the gate cleanly."
            )
        assemblies.append(
            {
                "assembly_id": f"asm-{index:03d}",
                "rationale": rationale,
                "member_clip_ids": clip_ids,
                "ordering": clip_ids,
                "bridge_cards": [],
                "title_card": None,
            }
        )
    return {
        "schema_version": "video_review_os.storyboard.v1",
        "provider": "fallback",
        "status": "fallback",
        "assemblies": assemblies,
    }


def _combine_candidates(
    candidates: list[dict[str, Any]],
    config: VideoReviewConfig,
) -> list[list[dict[str, Any]]]:
    """Conservatively group source-adjacent clips into stronger assemblies.

    Two heuristics, both narrative-safe because clip selection already produces clips in
    source order: attach a clip that starts mid-thought to its immediate predecessor (so the
    lead-in context rides along), and merge a too-short clip into the preceding group. Never
    reorders, never crosses a large source gap, caps group size at 3.
    """
    if not config.assembly.combine:
        return [[candidate] for candidate in candidates]

    ideal_min = config.gate.ideal_min_seconds
    max_group = 3
    groups: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        attach = False
        if groups and len(groups[-1]) < max_group:
            previous = groups[-1][-1]
            if _source_adjacent(previous, candidate) and (_has_lead_in(candidate) or _is_short(candidate, ideal_min)):
                attach = True
        if attach:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])
    return groups


def _has_lead_in(candidate: dict[str, Any]) -> bool:
    return any(
        op.get("op") == "add_lead_in" for op in candidate.get("quality_gate", {}).get("repair_ops", [])
    )


def _is_short(candidate: dict[str, Any], ideal_min: float) -> bool:
    return (float(candidate.get("end", 0.0)) - float(candidate.get("start", 0.0))) < ideal_min


def _source_adjacent(previous: dict[str, Any], candidate: dict[str, Any], max_gap: float = 2.5) -> bool:
    if previous.get("source_sha256") != candidate.get("source_sha256"):
        return False
    gap = float(candidate.get("start", 0.0)) - float(previous.get("end", 0.0))
    return -0.5 <= gap <= max_gap


def _sanitize_assemblies(
    raw_assemblies: Any,
    clips: dict[str, Any],
    config: VideoReviewConfig,
) -> list[dict[str, Any]]:
    """Drop reject/unknown members and renumber, so a hosted provider can't smuggle in a
    rejected clip or reference a clip that does not exist."""
    valid: dict[str, str] = {
        candidate["clip_id"]: str(candidate.get("decision", "")).lower()
        for candidate in clips.get("candidates", [])
    }
    cleaned: list[dict[str, Any]] = []
    index = 1
    for entry in raw_assemblies or []:
        ordering = [
            clip_id
            for clip_id in (entry.get("ordering") or entry.get("member_clip_ids") or [])
            if valid.get(clip_id) and valid.get(clip_id) != "reject"
        ]
        if not ordering:
            continue
        cleaned.append(
            {
                "assembly_id": f"asm-{index:03d}",
                "rationale": str(entry.get("rationale", "")),
                "member_clip_ids": ordering,
                "ordering": ordering,
                "bridge_cards": [
                    {
                        "after_clip_id": card.get("after_clip_id"),
                        "kind": str(card.get("kind", "bridge")),
                        "text": str(card.get("text", "")),
                    }
                    for card in entry.get("bridge_cards", [])
                    if card.get("after_clip_id") in ordering and str(card.get("text", "")).strip()
                ],
                "title_card": (
                    {"text": str(entry["title_card"].get("text", ""))}
                    if isinstance(entry.get("title_card"), dict) and str(entry["title_card"].get("text", "")).strip()
                    else None
                ),
            }
        )
        index += 1
    return cleaned


def propose_storyboard(project_dir: Path, config: VideoReviewConfig) -> Path:
    clips = read_json(project_dir / "clips.json")
    provider = provider_for(config.storyboard)
    storyboard = provider.propose(clips, config)
    storyboard["created_at"] = utc_now_iso()
    storyboard["project_id"] = clips["project_id"]
    storyboard["source_sha256"] = clips["source_sha256"]
    out = project_dir / "storyboard.json"
    atomic_write_json(out, storyboard)
    return out
