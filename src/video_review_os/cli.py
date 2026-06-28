from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Iterable

from .approval import approve_clip
from .clip_select import select_project_clips
from .config import VideoReviewConfig, write_example_config
from .copy import draft_project_copy
from .dashboard import build_dashboard
from .ingest import discover_videos, ingest_many, ingest_video
from .render import render_project
from .transcribe import transcribe_project


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

    if args.command == "render":
        include = tuple(args.include or config.render.default_decisions)
        print(render_project(resolve_project(args.project, config), config, include, args.dry_run))
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
        projects = run_once(config, render=args.render, include=args.include)
        json_path, html_path = build_dashboard(config, projects)
        for project in projects:
            print(project)
        print(json_path)
        print(html_path)
        return 0

    if args.command == "watch":
        while True:
            run_once(config, render=args.render, include=args.include)
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

    render = sub.add_parser("render", help="Render MP4 drafts for allowed decisions")
    render.add_argument("project")
    render.add_argument(
        "--include",
        action="append",
        choices=["keep", "trim", "review"],
        help="Decision to render. Defaults to keep only. Reject never renders.",
    )
    render.add_argument("--dry-run", action="store_true")

    dashboard = sub.add_parser("dashboard", help="Build static dashboard JSON and HTML")
    dashboard.add_argument("projects", nargs="*")

    approve = sub.add_parser("approve", help="Approve a clip signature for local review state")
    approve.add_argument("project")
    approve.add_argument("clip_id")
    approve.add_argument("--reviewer", default="local-reviewer")
    approve.add_argument("--notes", default="")

    run_once_cmd = sub.add_parser("run-once", help="Scan watch folder and run review artifacts once")
    run_once_cmd.add_argument("--render", action="store_true", help="Render default keep clips")
    run_once_cmd.add_argument("--include", action="append", choices=["keep", "trim", "review"])

    watch = sub.add_parser("watch", help="Poll the watch folder and refresh review artifacts")
    watch.add_argument("--interval", type=int, default=60)
    watch.add_argument("--render", action="store_true", help="Render default keep clips")
    watch.add_argument("--include", action="append", choices=["keep", "trim", "review"])

    return parser


def run_once(
    config: VideoReviewConfig,
    *,
    render: bool = False,
    include: Iterable[str] | None = None,
) -> list[Path]:
    videos = discover_videos(config.paths.watch_dir)
    projects = [ingest_video(video, config) for video in videos]
    for project in projects:
        transcribe_project(project, config)
        select_project_clips(project, config)
        draft_project_copy(project, config)
        if render:
            render_project(project, config, include)
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

