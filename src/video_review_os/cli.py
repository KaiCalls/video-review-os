from __future__ import annotations

import argparse
import time
from pathlib import Path

from .approval import approve_assembly, approve_clip
from .assembly import build_assemblies
from .captions import build_assembly_captions, write_project_captions
from .clip_select import select_project_clips
from .config import VideoReviewConfig, write_example_config
from .copy import draft_project_copy
from .dashboard import build_dashboard
from .ingest import discover_videos, ingest_many, ingest_video
from .posting import create_assembly_post_queue, create_post_queue
from .render import render_assemblies, render_project
from .scenes import extract_project_scenes
from .silence import detect_silence
from .storyboard import propose_storyboard
from .tagging import tag_project
from .transcribe import transcribe_project
from .visuals import make_project_visuals


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init-config":
        write_example_config(Path(args.path))
        print(f"Wrote {args.path}")
        return 0

    config = VideoReviewConfig.from_file(Path(args.config) if args.config else None)

    if args.command == "scan":
        videos = discover_videos(config.paths.watch_dir)
        for video in videos:
            print(video)
        print(f"{len(videos)} video(s)")
        return 0

    if args.command == "ingest":
        paths = [Path(path) for path in args.paths]
        projects = ingest_many(paths, config)
        for project in projects:
            print(project)
        return 0

    if args.command == "transcribe":
        print(transcribe_project(resolve_project(args.project, config), config))
        return 0

    if args.command == "select-clips":
        print(select_project_clips(resolve_project(args.project, config), config))
        return 0

    if args.command == "draft-copy":
        print(draft_project_copy(resolve_project(args.project, config), config))
        return 0

    if args.command == "tag":
        print(tag_project(resolve_project(args.project, config), config))
        return 0

    if args.command == "captions":
        print(write_project_captions(resolve_project(args.project, config), config))
        return 0

    if args.command == "scenes":
        print(
            extract_project_scenes(
                resolve_project(args.project, config),
                config,
                dry_run=args.dry_run,
                strict=args.strict,
            )
        )
        return 0

    if args.command == "silence":
        print(detect_silence(resolve_project(args.project, config), config))
        return 0

    if args.command == "visuals":
        print(make_project_visuals(resolve_project(args.project, config), config, dry_run=args.dry_run))
        return 0

    if args.command == "storyboard":
        print(propose_storyboard(resolve_project(args.project, config), config))
        return 0

    if args.command == "assemble":
        print(build_assemblies(resolve_project(args.project, config), config))
        return 0

    if args.command == "assembly-captions":
        print(build_assembly_captions(resolve_project(args.project, config), config))
        return 0

    if args.command == "render-assemblies":
        print(
            render_assemblies(
                resolve_project(args.project, config),
                config,
                dry_run=args.dry_run,
                burn_captions=args.burn_captions,
            )
        )
        return 0

    if args.command == "render":
        include = tuple(args.include or config.render.default_decisions)
        print(
            render_project(
                resolve_project(args.project, config),
                config,
                include,
                args.dry_run,
                burn_captions=args.burn_captions,
            )
        )
        return 0

    if args.command == "post-queue":
        print(
            create_post_queue(
                resolve_project(args.project, config),
                platform=args.platform,
                include_unapproved=args.include_unapproved,
            )
        )
        return 0

    if args.command == "assembly-post-queue":
        print(
            create_assembly_post_queue(
                resolve_project(args.project, config),
                platform=args.platform,
                include_unapproved=args.include_unapproved,
            )
        )
        return 0

    if args.command == "approve-assembly":
        print(
            approve_assembly(
                resolve_project(args.project, config),
                args.assembly_id,
                args.reviewer,
                args.notes,
            )
        )
        return 0

    if args.command == "dashboard":
        projects = [resolve_project(project, config) for project in args.projects] if args.projects else None
        json_path, html_path = build_dashboard(config, projects)
        print(json_path)
        print(html_path)
        return 0

    if args.command == "approve":
        print(approve_clip(resolve_project(args.project, config), args.clip_id, args.reviewer, args.notes))
        return 0

    if args.command == "run-once":
        projects = run_once(
            config,
            render=args.render,
            burn_captions=args.burn_captions,
            scenes=not args.no_scenes,
            visuals=not args.no_visuals,
            assemble=not args.no_assemble,
            post_queue=args.post_queue,
        )
        json_path, html_path = build_dashboard(config, projects)
        for project in projects:
            print(project)
        print(json_path)
        print(html_path)
        return 0

    if args.command == "watch":
        while True:
            run_once(
                config,
                render=args.render,
                burn_captions=args.burn_captions,
                scenes=not args.no_scenes,
                visuals=not args.no_visuals,
                assemble=not args.no_assemble,
                post_queue=args.post_queue,
            )
            build_dashboard(config)
            time.sleep(args.interval)

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-review-os",
        description="Local-first raw video review pipeline. Default behavior is review-only.",
    )
    parser.add_argument("--config", help="Path to config.toml")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init-config", help="Write an example config.toml")
    init.add_argument("--path", default="config.toml")

    sub.add_parser("scan", help="List videos in the configured watch folder")

    ingest = sub.add_parser("ingest", help="Create project folders for source videos")
    ingest.add_argument("paths", nargs="+")

    transcribe = sub.add_parser("transcribe", help="Create transcript.json for a project")
    transcribe.add_argument("project")

    select = sub.add_parser("select-clips", help="Create clips.json with quality gate decisions")
    select.add_argument("project")

    draft = sub.add_parser("draft-copy", help="Create draft copy sidecars")
    draft.add_argument("project")

    tag = sub.add_parser("tag", help="Write a content tag record (event/performer/venue/energy/orientation/bucket)")
    tag.add_argument("project")

    captions = sub.add_parser("captions", help="Write SRT/VTT caption sidecars for visible candidates")
    captions.add_argument("project")

    scenes = sub.add_parser("scenes", help="Extract representative frame images for clip review")
    scenes.add_argument("project")
    scenes.add_argument("--dry-run", action="store_true", help="Plan frame timestamps without calling ffmpeg")
    scenes.add_argument("--strict", action="store_true", help="Fail when frame extraction fails")

    visuals = sub.add_parser("visuals", help="Make SVG thumbnail and scene-card drafts from extracted frames")
    visuals.add_argument("project")
    visuals.add_argument("--dry-run", action="store_true")

    silence = sub.add_parser("silence", help="Detect audio dead-air (silencedetect) for the assembly layer to drop")
    silence.add_argument("project")

    storyboard = sub.add_parser("storyboard", help="Propose how clips become assemblies (repair/combine/reorder)")
    storyboard.add_argument("project")

    assemble = sub.add_parser("assemble", help="Resolve the storyboard into concrete multi-range edit drafts")
    assemble.add_argument("project")

    assembly_captions = sub.add_parser(
        "assembly-captions", help="Caption the assembled timeline (full sidecar + per-segment SRTs for burn-in)"
    )
    assembly_captions.add_argument("project")

    render_assemblies_cmd = sub.add_parser(
        "render-assemblies", help="Render the auto-generated assembly drafts (reject never renders)"
    )
    render_assemblies_cmd.add_argument("project")
    render_assemblies_cmd.add_argument("--dry-run", action="store_true")
    render_assemblies_cmd.add_argument(
        "--burn-captions", action="store_true", help="Burn captions onto the assembled draft"
    )

    render = sub.add_parser("render", help="Render single-clip MP4 drafts for allowed decisions (legacy path)")
    render.add_argument("project")
    render.add_argument(
        "--include",
        action="append",
        choices=["keep", "trim", "review"],
        help="Decision to render. Defaults to keep only. Reject never renders.",
    )
    render.add_argument("--dry-run", action="store_true")
    render.add_argument("--burn-captions", action="store_true", help="Burn existing SRT sidecars into MP4 renders")

    post_queue = sub.add_parser("post-queue", help="Create a manual post queue for approved rendered clips")
    post_queue.add_argument("project")
    post_queue.add_argument("--platform", default="generic")
    post_queue.add_argument("--include-unapproved", action="store_true", help="Include blocked items for review")

    assembly_post_queue = sub.add_parser(
        "assembly-post-queue", help="Create a manual post queue for approved rendered assemblies"
    )
    assembly_post_queue.add_argument("project")
    assembly_post_queue.add_argument("--platform", default="generic")
    assembly_post_queue.add_argument(
        "--include-unapproved", action="store_true", help="Include blocked items for review"
    )

    approve_assembly_cmd = sub.add_parser("approve-assembly", help="Approve an assembly signature for local review state")
    approve_assembly_cmd.add_argument("project")
    approve_assembly_cmd.add_argument("assembly_id")
    approve_assembly_cmd.add_argument("--reviewer", default="local-reviewer")
    approve_assembly_cmd.add_argument("--notes", default="")

    dashboard = sub.add_parser("dashboard", help="Build static dashboard JSON and HTML")
    dashboard.add_argument("projects", nargs="*")

    approve = sub.add_parser("approve", help="Approve a clip signature for local review state")
    approve.add_argument("project")
    approve.add_argument("clip_id")
    approve.add_argument("--reviewer", default="local-reviewer")
    approve.add_argument("--notes", default="")

    run_once_cmd = sub.add_parser("run-once", help="Scan watch folder and run review artifacts once")
    run_once_cmd.add_argument("--render", action="store_true", help="Render the auto-generated assembly drafts")
    run_once_cmd.add_argument("--burn-captions", action="store_true", help="Burn captions onto rendered assemblies")
    run_once_cmd.add_argument("--no-scenes", action="store_true", help="Skip representative frame extraction")
    run_once_cmd.add_argument("--no-visuals", action="store_true", help="Skip thumbnail and scene-card draft generation")
    run_once_cmd.add_argument("--no-assemble", action="store_true", help="Skip storyboard and assembly generation")
    run_once_cmd.add_argument("--post-queue", action="store_true", help="Write a manual assembly post queue artifact")

    watch = sub.add_parser("watch", help="Poll the watch folder and refresh review artifacts")
    watch.add_argument("--interval", type=int, default=60)
    watch.add_argument("--render", action="store_true", help="Render the auto-generated assembly drafts")
    watch.add_argument("--burn-captions", action="store_true", help="Burn captions onto rendered assemblies")
    watch.add_argument("--no-scenes", action="store_true", help="Skip representative frame extraction")
    watch.add_argument("--no-visuals", action="store_true", help="Skip thumbnail and scene-card draft generation")
    watch.add_argument("--no-assemble", action="store_true", help="Skip storyboard and assembly generation")
    watch.add_argument("--post-queue", action="store_true", help="Write a manual assembly post queue artifact")

    return parser


def run_once(
    config: VideoReviewConfig,
    *,
    render: bool = False,
    burn_captions: bool = False,
    scenes: bool = True,
    visuals: bool = True,
    assemble: bool = True,
    post_queue: bool = False,
) -> list[Path]:
    videos = discover_videos(config.paths.watch_dir)
    projects = [ingest_video(video, config) for video in videos]
    for project in projects:
        transcribe_project(project, config)
        select_project_clips(project, config)
        tag_project(project, config)
        draft_project_copy(project, config)
        write_project_captions(project, config)
        if scenes:
            extract_project_scenes(project, config)
        if visuals:
            make_project_visuals(project, config)
        if assemble:
            detect_silence(project, config)
            propose_storyboard(project, config)
            build_assemblies(project, config)
            build_assembly_captions(project, config)
            if render:
                render_assemblies(project, config, burn_captions=burn_captions)
        if post_queue:
            create_assembly_post_queue(project)
    return projects


def resolve_project(value: str | Path, config: VideoReviewConfig) -> Path:
    path = Path(value)
    if path.exists():
        return path.resolve()
    candidate = config.paths.projects_dir / str(value)
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(f"Project not found: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
