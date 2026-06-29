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
    return _chunk_cues(words, max_chars, max_seconds)


def _chunk_cues(words: list[dict[str, Any]], max_chars: int, max_seconds: float) -> list[dict[str, Any]]:
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


def build_assembly_captions(project_dir: Path, config: VideoReviewConfig) -> Path:
    """Caption the *final assembled timeline*, not just the source clips.

    For each assembly we walk the EDL in order, re-base each source segment's words to
    its position on the output timeline, and write both a full-timeline SRT/VTT sidecar
    and per-segment SRTs (segment-local) that the renderer burns into each source cut.
    Card segments advance the clock but carry no cues (the card shows its own text).
    Deterministic: missing word timings fall back to the clip text as a single cue.
    """
    clips = read_json(project_dir / "clips.json")
    assemblies = read_json(project_dir / "assemblies.json")
    words_by_clip = {c["clip_id"]: c.get("words", []) for c in clips.get("candidates", [])}
    text_by_clip = {c["clip_id"]: str(c.get("text", "")) for c in clips.get("candidates", [])}
    max_chars = config.captions.max_chars
    max_seconds = config.captions.max_seconds
    captions_dir = ensure_dir(project_dir / "captions" / "assemblies")

    records = []
    for assembly in assemblies.get("assemblies", []):
        assembly_id = assembly["assembly_id"]
        seg_dir = ensure_dir(captions_dir / assembly_id)
        out_offset = 0.0
        full_cues: list[dict[str, Any]] = []
        segment_captions = []
        for idx, segment in enumerate(assembly.get("segments", []), start=1):
            if segment.get("kind") == "source":
                seg_start = float(segment["start"])
                seg_end = float(segment["end"])
                words = _relative_words(words_by_clip.get(segment.get("from_clip_id"), []), seg_start, seg_end)
                if words:
                    local = _chunk_cues(words, max_chars, max_seconds)
                else:
                    text = text_by_clip.get(segment.get("from_clip_id"), "").strip() or "Review this clip before posting."
                    local = [{"index": 1, "start": 0.0, "end": max(0.1, seg_end - seg_start), "text": text[:max_chars]}]
                srt_path = seg_dir / f"seg-{idx:03d}.srt"
                atomic_write_text(srt_path, cues_to_srt(local))
                segment_captions.append(
                    {"segment_index": idx, "srt_path": str(srt_path), "cue_count": len(local)}
                )
                for cue in local:
                    full_cues.append(
                        {
                            "index": len(full_cues) + 1,
                            "start": round(cue["start"] + out_offset, 3),
                            "end": round(cue["end"] + out_offset, 3),
                            "text": cue["text"],
                        }
                    )
                out_offset += max(0.0, seg_end - seg_start)
            else:
                out_offset += float(segment.get("duration", 0.0))

        full_srt = captions_dir / f"{assembly_id}.srt"
        full_vtt = captions_dir / f"{assembly_id}.vtt"
        atomic_write_text(full_srt, cues_to_srt(full_cues))
        atomic_write_text(full_vtt, cues_to_vtt(full_cues))
        records.append(
            {
                "assembly_id": assembly_id,
                "status": "written",
                "srt_path": str(full_srt),
                "vtt_path": str(full_vtt),
                "cue_count": len(full_cues),
                "segment_captions": segment_captions,
            }
        )

    artifact = {
        "schema_version": "video_review_os.assembly_captions.v1",
        "created_at": utc_now_iso(),
        "project_id": clips["project_id"],
        "source_sha256": clips["source_sha256"],
        "captions": records,
    }
    out = project_dir / "assembly_captions.json"
    atomic_write_json(out, artifact)
    return out


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
