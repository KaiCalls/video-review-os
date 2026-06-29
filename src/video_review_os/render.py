from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .assembly import assembly_is_renderable
from .captions import build_assembly_captions
from .config import VideoReviewConfig
from .utils import atomic_write_json, ensure_dir, read_json, slugify, utc_now_iso


def is_renderable(candidate: dict[str, Any], include_decisions: Iterable[str] | None = None) -> bool:
    decision = str(candidate.get("decision", "")).lower()
    if decision == "reject":
        return False
    allowed = set(include_decisions or ("keep",))
    return decision in allowed


def render_project(
    project_dir: Path,
    config: VideoReviewConfig,
    include_decisions: Iterable[str] | None = None,
    dry_run: bool = False,
    burn_captions: bool = False,
) -> Path:
    source = read_json(project_dir / "source.json")
    clips = read_json(project_dir / "clips.json")
    video_path = Path(source["source"]["active_path"])
    include = tuple(include_decisions or config.render.default_decisions)
    renders = []
    for candidate in clips.get("candidates", []):
        if not is_renderable(candidate, include):
            renders.append(
                {
                    "clip_id": candidate["clip_id"],
                    "decision": candidate["decision"],
                    "status": "skipped",
                    "reason": "Decision is not renderable under current policy.",
                }
            )
            continue
        caption_path = project_dir / "captions" / f"{candidate['clip_id']}.srt"
        active_caption = caption_path if burn_captions and caption_path.exists() else None
        suffix = f"{candidate['clip_id']}-{slugify(candidate.get('decision', 'draft'))}"
        if active_caption:
            suffix = f"{suffix}-captioned"
        output = project_dir / "renders" / f"{suffix}.mp4"
        if dry_run:
            renders.append(
                {
                    "clip_id": candidate["clip_id"],
                    "decision": candidate["decision"],
                    "status": "dry-run",
                    "output_path": str(output),
                    "burned_captions": active_caption is not None,
                    "caption_path": str(active_caption) if active_caption else None,
                }
            )
            continue
        render_clip(
            video_path,
            output,
            float(candidate["start"]),
            float(candidate["end"]),
            config,
            caption_path=active_caption,
        )
        renders.append(
            {
                "clip_id": candidate["clip_id"],
                "decision": candidate["decision"],
                "status": "rendered",
                "output_path": str(output),
                "burned_captions": active_caption is not None,
                "caption_path": str(active_caption) if active_caption else None,
            }
        )
    artifact = {
        "schema_version": "video_review_os.renders.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "include_decisions": list(include),
        "reject_never_renders": True,
        "auto_publish_enabled": False,
        "burn_captions_requested": burn_captions,
        "renders": renders,
    }
    out = project_dir / "renders.json"
    atomic_write_json(out, artifact)
    return out


def render_clip(
    video_path: Path,
    output_path: Path,
    start: float,
    end: float,
    config: VideoReviewConfig,
    *,
    caption_path: Path | None = None,
) -> None:
    ensure_dir(output_path.parent)
    duration = max(0.0, end - start)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    subtitle_args = ["-vf", _subtitle_filter(caption_path)] if caption_path else []
    command = [
        config.media.ffmpeg_path,
        "-hide_banner",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration:.3f}",
        *subtitle_args,
        "-c:v",
        config.render.video_codec,
        "-preset",
        config.render.preset,
        "-crf",
        str(config.render.crf),
        "-c:a",
        config.render.audio_codec,
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(tmp_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"ffmpeg not found: {config.media.ffmpeg_path}") from exc
    except subprocess.CalledProcessError as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(exc.stderr.strip() or str(exc)) from exc
    os.replace(tmp_path, output_path)


def _subtitle_filter(path: Path) -> str:
    value = path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
    return f"subtitles='{value}'"


def plan_assembly_render(assembly: dict[str, Any], project_dir: Path, config: VideoReviewConfig) -> dict[str, Any]:
    """Pure planning step (no ffmpeg). Resolves output paths and per-segment specs so the
    render can be inspected, dry-run, and unit-tested without invoking ffmpeg."""
    assembly_id = assembly["assembly_id"]
    base = project_dir / "renders" / "assemblies"
    output_path = base / f"{assembly_id}.mp4"
    work_dir = base / assembly_id
    captions_dir = project_dir / "captions" / "assemblies" / assembly_id
    segments = assembly.get("segments", [])
    single_pass = len(segments) == 1 and segments[0].get("kind") == "source"

    segment_plan: list[dict[str, Any]] = []
    for idx, segment in enumerate(segments, start=1):
        intermediate = work_dir / f"seg-{idx:03d}.mp4"
        if segment.get("kind") == "source":
            start = float(segment["start"])
            end = float(segment["end"])
            segment_plan.append(
                {
                    "kind": "source",
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(max(0.0, end - start), 3),
                    "from_clip_id": segment.get("from_clip_id"),
                    "intermediate": str(intermediate),
                    "caption_path": str(captions_dir / f"seg-{idx:03d}.srt"),
                }
            )
        else:
            segment_plan.append(
                {
                    "kind": "card",
                    "card_kind": segment.get("card_kind", "bridge"),
                    "text": segment.get("text", ""),
                    "duration": round(float(segment.get("duration", config.assembly.card_seconds)), 3),
                    "intermediate": str(intermediate),
                }
            )
    return {
        "assembly_id": assembly_id,
        "kind": assembly.get("kind", "single"),
        "output_path": str(output_path),
        "concat_list": str(work_dir / "concat.txt"),
        "single_pass": single_pass,
        "segments": segment_plan,
    }


def _prior_renders(project_dir: Path) -> dict[str, dict[str, Any]]:
    path = project_dir / "assembly_renders.json"
    if not path.exists():
        return {}
    return {record.get("assembly_id"): record for record in read_json(path).get("renders", [])}


def render_assemblies(
    project_dir: Path,
    config: VideoReviewConfig,
    *,
    dry_run: bool = False,
    burn_captions: bool = False,
) -> Path:
    source = read_json(project_dir / "source.json")
    assemblies = read_json(project_dir / "assemblies.json")
    video_path = Path(source["source"]["active_path"])
    # Caption the final assembled timeline so burned-in captions always match the EDL.
    if burn_captions and not dry_run:
        build_assembly_captions(project_dir, config)
    prior = _prior_renders(project_dir)
    records = []
    for assembly in assemblies.get("assemblies", []):
        plan = plan_assembly_render(assembly, project_dir, config)
        if not assembly_is_renderable(assembly):
            records.append(
                {
                    "assembly_id": assembly["assembly_id"],
                    "status": "skipped",
                    "reason": "Assembly is not renderable (reject member or no source range).",
                }
            )
            continue
        previous = prior.get(assembly["assembly_id"])
        if (
            not dry_run
            and previous is not None
            and previous.get("status") in {"rendered", "cached"}
            and previous.get("assembly_signature") == assembly.get("assembly_signature")
            and bool(previous.get("burned_captions")) == bool(burn_captions)
            and previous.get("output_path")
            and Path(previous["output_path"]).exists()
        ):
            # Same EDL, same caption state, output still on disk — don't re-encode.
            records.append({**previous, "status": "cached"})
            continue
        if dry_run:
            records.append(
                {
                    "assembly_id": assembly["assembly_id"],
                    "status": "dry-run",
                    "output_path": plan["output_path"],
                    "segment_count": len(plan["segments"]),
                    "single_pass": plan["single_pass"],
                    "burn_captions": burn_captions,
                }
            )
            continue
        render_assembly(video_path, plan, config, burn_captions=burn_captions)
        records.append(
            {
                "assembly_id": assembly["assembly_id"],
                "status": "rendered",
                "output_path": plan["output_path"],
                "segment_count": len(plan["segments"]),
                "single_pass": plan["single_pass"],
                "burned_captions": burn_captions,
                # Bind the render to the exact EDL it came from, so a stale render can't be
                # served under a later approval (see posting._assembly_renders_by_id).
                "assembly_signature": assembly.get("assembly_signature"),
                "total_duration": assembly.get("total_duration"),
            }
        )
    artifact = {
        "schema_version": "video_review_os.assembly_renders.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "reject_never_renders": True,
        "auto_publish_enabled": False,
        "burn_captions_requested": burn_captions,
        "renders": records,
    }
    out = project_dir / "assembly_renders.json"
    atomic_write_json(out, artifact)
    return out


def render_assembly(
    video_path: Path,
    plan: dict[str, Any],
    config: VideoReviewConfig,
    *,
    burn_captions: bool = False,
) -> None:
    """Render the whole assembly in a SINGLE ffmpeg pass via filter_complex.

    One pass (no per-segment intermediate files, no concat demuxer) avoids the AAC
    encoder-priming drift that accumulates when you concat-copy many re-encoded mp4s. The
    graph reframes each source range, burns segment-local captions, draws card text, then
    concats, loudness-normalizes, and (optionally) mixes a ducked music bed.
    """
    output_path = Path(plan["output_path"])
    ensure_dir(output_path.parent)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    input_args, filter_complex, audio_out = _build_assembly_filtergraph(video_path, plan, config, burn_captions)
    command = [
        config.media.ffmpeg_path,
        "-hide_banner",
        "-y",
        *input_args,
        "-filter_complex",
        filter_complex,
        "-map",
        "[cv]",
        "-map",
        audio_out,
        "-c:v",
        config.render.video_codec,
        "-preset",
        config.render.preset,
        "-crf",
        str(config.render.crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        config.render.audio_codec,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(tmp_path),
    ]
    _run_ffmpeg(command, config, tmp_path, output_path)


def _build_assembly_filtergraph(
    video_path: Path,
    plan: dict[str, Any],
    config: VideoReviewConfig,
    burn_captions: bool,
) -> tuple[list[str], str, str]:
    """Build (input_args, filter_complex, audio_out_label). Pure except for writing card
    text sidecars, so the graph string can be inspected/tested without invoking ffmpeg."""
    asm = config.assembly
    width, height, fps = asm.width, asm.height, asm.fps
    input_args: list[str] = []
    nodes: list[str] = []
    pairs: list[tuple[str, str]] = []
    ff = 0  # ffmpeg input index

    for position, segment in enumerate(plan["segments"], start=1):
        vlabel, alabel = f"v{position}", f"a{position}"
        if segment["kind"] == "source":
            start = float(segment["start"])
            duration = float(segment["duration"])
            input_args += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video_path)]
            caption = _segment_caption(segment, burn_captions)
            nodes += _reframe_chain(f"[{ff}:v]", vlabel, position, config, caption)
            nodes.append(
                f"[{ff}:a]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo[{alabel}]"
            )
            ff += 1
        else:
            duration = float(segment["duration"])
            input_args += [
                "-f", "lavfi", "-t", f"{duration:.3f}",
                "-i", f"color=c={_ff_color(asm.card_background)}:s={width}x{height}:r={fps}",
            ]
            nodes.append(_card_video_node(f"[{ff}:v]", vlabel, segment, config))
            ff += 1
            input_args += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            nodes.append(f"[{ff}:a]aformat=sample_rates=48000:channel_layouts=stereo[{alabel}]")
            ff += 1
        pairs.append((vlabel, alabel))

    concat_inputs = "".join(f"[{v}][{a}]" for v, a in pairs)
    nodes.append(f"{concat_inputs}concat=n={len(pairs)}:v=1:a=1[cv][ca]")
    nodes.append(f"[ca]loudnorm=I={asm.loudness_lufs}:TP=-1.5:LRA=11[cn]")
    audio_out = "[cn]"

    music = asm.music_path.strip()
    if music and Path(music).exists():
        input_args += ["-stream_loop", "-1", "-i", music]
        nodes.append(
            f"[{ff}:a]volume={asm.music_volume},aformat=sample_rates=48000:channel_layouts=stereo[mv]"
        )
        nodes.append("[cn][mv]amix=inputs=2:duration=first:normalize=0[outa]")
        audio_out = "[outa]"
        ff += 1

    return input_args, ";".join(nodes), audio_out


def _reframe_chain(
    source: str,
    out_label: str,
    position: int,
    config: VideoReviewConfig,
    caption_path: Path | None,
) -> list[str]:
    asm = config.assembly
    width, height, fps = asm.width, asm.height, asm.fps
    sub = ""
    if caption_path is not None:
        sub = f",subtitles='{_ff_filter_path(caption_path)}':force_style='{asm.caption_style}'"
    if asm.fit_mode == "crop":
        return [
            f"{source}scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}{sub},format=yuv420p[{out_label}]"
        ]
    if asm.fit_mode == "pad":
        bg = _ff_color(asm.card_background)
        return [
            f"{source}scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1,fps={fps}{sub},format=yuv420p[{out_label}]"
        ]
    # blur (default): contained foreground over a blurred fill, no black bars.
    return [
        f"{source}split=2[bg{position}][fg{position}]",
        f"[bg{position}]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=20:1,setsar=1[bgb{position}]",
        f"[fg{position}]scale={width}:{height}:force_original_aspect_ratio=decrease[fg2{position}]",
        f"[bgb{position}][fg2{position}]overlay=(W-w)/2:(H-h)/2,fps={fps}{sub},format=yuv420p[{out_label}]",
    ]


def _card_video_node(source: str, out_label: str, segment: dict[str, Any], config: VideoReviewConfig) -> str:
    text = str(segment.get("text", "")).strip()
    font = _locate_font(config)
    if font and text:
        text_path = Path(segment["intermediate"]).with_suffix(".card.txt")
        ensure_dir(text_path.parent)
        text_path.write_text(_wrap_card_text(text), encoding="utf-8")
        draw = (
            f"drawtext=fontfile='{_ff_filter_path(Path(font))}'"
            f":textfile='{_ff_filter_path(text_path)}'"
            f":fontcolor={_ff_color(config.assembly.card_text_color)}"
            f":fontsize={config.assembly.card_font_size}"
            ":x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=18:expansion=none"
        )
        return f"{source}{draw},setsar=1,format=yuv420p[{out_label}]"
    return f"{source}setsar=1,format=yuv420p[{out_label}]"


def _segment_caption(segment: dict[str, Any], burn_captions: bool) -> Path | None:
    if not burn_captions:
        return None
    raw = segment.get("caption_path")
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() else None


def _ff_color(value: str) -> str:
    value = value.strip()
    if value.startswith("#"):
        return "0x" + value[1:]
    return value


def _run_ffmpeg(command: list[str], config: VideoReviewConfig, tmp_path: Path, output_path: Path) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"ffmpeg not found: {config.media.ffmpeg_path}") from exc
    except subprocess.CalledProcessError as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(exc.stderr.strip() or str(exc)) from exc
    os.replace(tmp_path, output_path)


def _locate_font(config: VideoReviewConfig) -> str | None:
    configured = config.assembly.card_font_path.strip()
    candidates = [configured] if configured else []
    candidates += [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _wrap_card_text(text: str, max_chars: int = 22, max_lines: int = 5) -> str:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join([*current, word])
        if current and len(trial) > max_chars:
            lines.append(" ".join(current))
            current = [word]
            if len(lines) == max_lines:
                break
        else:
            current.append(word)
    if current and len(lines) < max_lines:
        lines.append(" ".join(current))
    return "\n".join(lines)


def _ff_filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
