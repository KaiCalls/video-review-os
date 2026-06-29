from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import VideoReviewConfig
from .utils import atomic_write_json, atomic_write_text, ensure_dir, read_json, utc_now_iso


def write_project_captions(project_dir: Path, config: VideoReviewConfig) -> Path:
    clips = read_json(project_dir / "clips.json")
    include = set(config.captions.include_decisions)
    captions = []
    captions_dir = ensure_dir(project_dir / "captions")

    for candidate in clips.get("candidates", []):
        decision = str(candidate.get("decision", ""))
        if decision not in include:
            captions.append(
                {
                    "clip_id": candidate["clip_id"],
                    "decision": decision,
                    "status": "skipped",
                    "reason": "Decision is not configured for captions.",
                }
            )
            continue

        cues = build_caption_cues(
            candidate,
            max_chars=config.captions.max_chars,
            max_seconds=config.captions.max_seconds,
        )
        srt_path = captions_dir / f"{candidate['clip_id']}.srt"
        vtt_path = captions_dir / f"{candidate['clip_id']}.vtt"
        atomic_write_text(srt_path, cues_to_srt(cues))
        atomic_write_text(vtt_path, cues_to_vtt(cues))
        captions.append(
            {
                "clip_id": candidate["clip_id"],
                "decision": decision,
                "status": "written",
                "srt_path": str(srt_path),
                "vtt_path": str(vtt_path),
                "cue_count": len(cues),
            }
        )

    artifact = {
        "schema_version": "video_review_os.captions.v1",
        "created_at": utc_now_iso(),
        "project_id": clips["project_id"],
        "source_sha256": clips["source_sha256"],
        "include_decisions": list(config.captions.include_decisions),
        "captions": captions,
    }
    out = project_dir / "captions.json"
    atomic_write_json(out, artifact)
    return out


def build_caption_cues(
    candidate: dict[str, Any],
    *,
    max_chars: int = 42,
    max_seconds: float = 3.5,
) -> list[dict[str, Any]]:
    clip_start = float(candidate.get("start", 0.0))
    clip_end = float(candidate.get("end", clip_start))
    words = _relative_words(candidate.get("words", []), clip_start, clip_end)
    if not words:
        text = str(candidate.get("text", "")).strip() or "Review this clip before posting."
        return [{"index": 1, "start": 0.0, "end": max(0.1, clip_end - clip_start), "text": text[:max_chars]}]

    cues: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        cues.append(
            {
                "index": len(cues) + 1,
                "start": current[0]["start"],
                "end": max(current[-1]["end"], current[0]["start"] + 0.1),
                "text": " ".join(word["word"] for word in current).strip(),
            }
        )
        current = []

    for word in words:
        next_text = " ".join([*(item["word"] for item in current), word["word"]]).strip()
        cue_seconds = word["end"] - current[0]["start"] if current else 0.0
        if current and (len(next_text) > max_chars or cue_seconds > max_seconds):
            flush()
        current.append(word)
    flush()
    return cues


def cues_to_srt(cues: list[dict[str, Any]]) -> str:
    blocks = []
    for cue in cues:
        blocks.append(
            "\n".join(
                [
                    str(cue["index"]),
                    f"{_srt_time(float(cue['start']))} --> {_srt_time(float(cue['end']))}",
                    str(cue["text"]),
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def cues_to_vtt(cues: list[dict[str, Any]]) -> str:
    blocks = ["WEBVTT"]
    for cue in cues:
        blocks.append(
            "\n".join(
                [
                    f"{_vtt_time(float(cue['start']))} --> {_vtt_time(float(cue['end']))}",
                    str(cue["text"]),
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def _relative_words(raw_words: Any, clip_start: float, clip_end: float) -> list[dict[str, Any]]:
    words = []
    for item in raw_words or []:
        word = str(item.get("word", "")).strip()
        if not word:
            continue
        try:
            start = round(max(0.0, float(item["start"]) - clip_start), 3)
            end = round(min(max(0.0, clip_end - clip_start), float(item["end"]) - clip_start), 3)
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            words.append({"word": word, "start": start, "end": end})
    return sorted(words, key=lambda item: item["start"])


def _srt_time(seconds: float) -> str:
    hours, remainder = divmod(max(0.0, seconds), 3600)
    minutes, remainder = divmod(remainder, 60)
    whole = int(remainder)
    millis = int(round((remainder - whole) * 1000))
    if millis == 1000:
        whole += 1
        millis = 0
    return f"{int(hours):02d}:{int(minutes):02d}:{whole:02d},{millis:03d}"


def _vtt_time(seconds: float) -> str:
    return _srt_time(seconds).replace(",", ".")
