from __future__ import annotations

import base64
import html
import mimetypes
from pathlib import Path
from typing import Any

from .config import VideoReviewConfig
from .utils import atomic_write_json, atomic_write_text, ensure_dir, read_json, utc_now_iso


def make_project_visuals(project_dir: Path, config: VideoReviewConfig, *, dry_run: bool = False) -> Path:
    source = read_json(project_dir / "source.json")
    clips = read_json(project_dir / "clips.json")
    drafts = _drafts_by_clip(project_dir)
    scenes = _scenes_by_clip(project_dir)
    include = set(config.visuals.include_decisions)
    thumbnails_dir = ensure_dir(project_dir / "visuals" / "thumbnails")
    scene_cards_dir = ensure_dir(project_dir / "visuals" / "scene-cards")
    mascot_uri = _image_data_uri(config.visuals.mascot_image_path)
    logo_uri = _image_data_uri(config.visuals.logo_image_path)
    visuals = []

    for candidate in clips.get("candidates", []):
        decision = str(candidate.get("decision", ""))
        if decision not in include:
            visuals.append(
                {
                    "clip_id": candidate["clip_id"],
                    "decision": decision,
                    "status": "skipped",
                    "reason": "Decision is not configured for visual drafts.",
                }
            )
            continue

        clip_id = candidate["clip_id"]
        frame_paths = _written_frame_paths(scenes.get(clip_id, {}))
        frame_uris = [_image_data_uri(path) for path in frame_paths]
        frame_uris = [uri for uri in frame_uris if uri]
        copy = _visual_copy(candidate, drafts.get(clip_id, {}))
        thumbnail_path = thumbnails_dir / f"{clip_id}.svg"
        scene_card_path = scene_cards_dir / f"{clip_id}.svg"

        if not dry_run:
            atomic_write_text(
                thumbnail_path,
                _thumbnail_svg(
                    config,
                    copy=copy,
                    decision=decision,
                    frame_uri=frame_uris[0] if frame_uris else None,
                    mascot_uri=mascot_uri,
                    logo_uri=logo_uri,
                ),
            )
            atomic_write_text(
                scene_card_path,
                _scene_card_svg(
                    config,
                    copy=copy,
                    decision=decision,
                    frame_uris=frame_uris[:3],
                    mascot_uri=mascot_uri,
                    logo_uri=logo_uri,
                ),
            )

        visuals.append(
            {
                "clip_id": clip_id,
                "decision": decision,
                "status": "dry-run" if dry_run else "written",
                "thumbnail_svg": str(thumbnail_path),
                "scene_card_svg": str(scene_card_path),
                "frame_count": len(frame_paths),
                "input_frames": [str(path) for path in frame_paths],
                "uses_mascot": mascot_uri is not None,
                "uses_logo": logo_uri is not None,
                "title": copy["title"],
                "hook": copy["hook"],
            }
        )

    artifact = {
        "schema_version": "video_review_os.visuals.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "include_decisions": list(config.visuals.include_decisions),
        "outputs": {
            "thumbnails_dir": str(thumbnails_dir),
            "scene_cards_dir": str(scene_cards_dir),
        },
        "visuals": visuals,
    }
    out = project_dir / "visuals.json"
    atomic_write_json(out, artifact)
    return out


def _drafts_by_clip(project_dir: Path) -> dict[str, dict[str, Any]]:
    path = project_dir / "drafts" / "copy.json"
    if not path.exists():
        return {}
    artifact = read_json(path)
    return {draft["clip_id"]: draft for draft in artifact.get("drafts", [])}


def _scenes_by_clip(project_dir: Path) -> dict[str, dict[str, Any]]:
    path = project_dir / "scenes.json"
    if not path.exists():
        return {}
    artifact = read_json(path)
    return {scene["clip_id"]: scene for scene in artifact.get("scenes", [])}


def _written_frame_paths(scene: dict[str, Any]) -> list[Path]:
    frames = []
    for frame in scene.get("frames", []):
        path = Path(str(frame.get("path", "")))
        if frame.get("status") == "written" and path.exists():
            frames.append(path)
    return frames


def _visual_copy(candidate: dict[str, Any], draft: dict[str, Any]) -> dict[str, str]:
    title = str(draft.get("title") or candidate.get("text") or "Review clip").strip()
    hook = str(draft.get("hook") or title).strip()
    caption = str(draft.get("caption") or candidate.get("text") or "").strip()
    return {
        "title": _truncate(title, 72),
        "hook": _truncate(hook, 92),
        "caption": _truncate(caption, 160),
    }


def _thumbnail_svg(
    config: VideoReviewConfig,
    *,
    copy: dict[str, str],
    decision: str,
    frame_uri: str | None,
    mascot_uri: str | None,
    logo_uri: str | None,
) -> str:
    width = config.visuals.thumbnail_width
    height = config.visuals.thumbnail_height
    text_x = 72
    title_lines = _text_lines(copy["title"], 28, 3)
    title_svg = _svg_lines(title_lines, text_x, height - 210, 66, 58, config.visuals.text_color, 800)
    hook_svg = _svg_lines(_text_lines(copy["hook"], 42, 2), text_x, height - 70, 30, 34, "#e5e7eb", 600)
    frame = (
        f'<image href="{_xml(frame_uri)}" x="0" y="0" width="{width}" height="{height}" preserveAspectRatio="xMidYMid slice" />'
        if frame_uri
        else _placeholder_frame(width, height, config.visuals.background)
    )
    mascot = (
        f'  <image href="{_xml(mascot_uri)}" x="{width - 310}" y="{height - 330}" width="240" height="260" preserveAspectRatio="xMidYMid meet" />'
        if mascot_uri
        else ""
    )
    logo = (
        f'  <image href="{_xml(logo_uri)}" x="{width - 180}" y="52" width="110" height="70" preserveAspectRatio="xMidYMid meet" />'
        if logo_uri
        else ""
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  <title>{_xml(copy["title"])}</title>
  <rect width="{width}" height="{height}" fill="{_xml(config.visuals.background)}" />
  {frame}
  <rect width="{width}" height="{height}" fill="#000000" opacity="0.48" />
  <rect x="56" y="48" width="176" height="48" rx="24" fill="{_xml(config.visuals.brand_accent)}" />
  <text x="88" y="79" font-family="Arial, sans-serif" font-size="22" font-weight="700" fill="#ffffff">{_xml(decision.upper())}</text>
{logo}
  {title_svg}
  {hook_svg}
{mascot}
</svg>
"""


def _scene_card_svg(
    config: VideoReviewConfig,
    *,
    copy: dict[str, str],
    decision: str,
    frame_uris: list[str],
    mascot_uri: str | None,
    logo_uri: str | None,
) -> str:
    width = config.visuals.scene_card_width
    height = config.visuals.scene_card_height
    frame_h = 360
    top = 74
    frames_svg = []
    for idx in range(3):
        y = top + (idx * (frame_h + 24))
        uri = frame_uris[idx] if idx < len(frame_uris) else None
        frames_svg.append(f'<rect x="72" y="{y}" width="{width - 144}" height="{frame_h}" rx="24" fill="#1f2937" />')
        if uri:
            frames_svg.append(
                f'<image href="{_xml(uri)}" x="72" y="{y}" width="{width - 144}" height="{frame_h}" preserveAspectRatio="xMidYMid slice" clip-path="inset(0 round 24px)" />'
            )
        else:
            frames_svg.append(
                f'<text x="{width / 2:.0f}" y="{y + (frame_h / 2):.0f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="28" fill="#9ca3af">Scene frame pending</text>'
            )
    text_y = top + (3 * (frame_h + 24)) + 64
    title_svg = _svg_lines(_text_lines(copy["title"], 24, 3), 72, text_y, 58, 58, config.visuals.text_color, 800)
    hook_svg = _svg_lines(_text_lines(copy["hook"], 36, 3), 72, text_y + 210, 32, 38, "#d1d5db", 600)
    mascot = (
        f'  <image href="{_xml(mascot_uri)}" x="{width - 300}" y="{height - 330}" width="220" height="260" preserveAspectRatio="xMidYMid meet" />'
        if mascot_uri
        else ""
    )
    logo = (
        f'  <image href="{_xml(logo_uri)}" x="{width - 190}" y="{height - 132}" width="110" height="70" preserveAspectRatio="xMidYMid meet" />'
        if logo_uri
        else ""
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
  <title>{_xml(copy["title"])}</title>
  <rect width="{width}" height="{height}" fill="{_xml(config.visuals.background)}" />
  <rect x="72" y="30" width="176" height="44" rx="22" fill="{_xml(config.visuals.brand_accent)}" />
  <text x="104" y="59" font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="#ffffff">{_xml(decision.upper())}</text>
  {"".join(frames_svg)}
  <rect x="0" y="{text_y - 34}" width="{width}" height="{height - text_y + 34}" fill="#000000" opacity="0.24" />
  {title_svg}
  {hook_svg}
{mascot}
{logo}
</svg>
"""


def _image_data_uri(path: Path | None) -> str | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _placeholder_frame(width: int, height: int, background: str) -> str:
    return f"""<rect x="0" y="0" width="{width}" height="{height}" fill="{_xml(background)}" />
  <path d="M0 {height * 0.72:.0f} C {width * 0.22:.0f} {height * 0.58:.0f}, {width * 0.46:.0f} {height * 0.92:.0f}, {width} {height * 0.70:.0f}" fill="#1f2937" opacity="0.9" />
  <path d="M0 {height * 0.84:.0f} C {width * 0.36:.0f} {height * 0.62:.0f}, {width * 0.66:.0f} {height * 0.98:.0f}, {width} {height * 0.78:.0f}" fill="#374151" opacity="0.75" />"""


def _svg_lines(
    lines: list[str],
    x: int,
    y: int,
    size: int,
    line_height: int,
    fill: str,
    weight: int,
) -> str:
    text = []
    for idx, line in enumerate(lines):
        text.append(
            f'<text x="{x}" y="{y + (idx * line_height)}" font-family="Arial, sans-serif" font-size="{size}" font-weight="{weight}" fill="{_xml(fill)}">{_xml(line)}</text>'
        )
    return "\n  ".join(text)


def _text_lines(text: str, max_chars: int, max_lines: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word]).strip()
        if current and len(candidate) > max_chars:
            lines.append(" ".join(current))
            current = [word]
            if len(lines) == max_lines:
                break
        else:
            current.append(word)
    if current and len(lines) < max_lines:
        lines.append(" ".join(current))
    if not lines:
        return ["Review clip"]
    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = _truncate(lines[-1], max_chars)
    return lines


def _truncate(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip(" ,.;:") + "..."


def _xml(value: str | None) -> str:
    return html.escape(str(value or ""), quote=True)
