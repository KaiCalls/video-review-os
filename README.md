# Video Review OS

Video Review OS is a local-first pipeline that turns raw videos into reviewable posting drafts. It watches or scans a folder, creates inspectable project folders, transcribes video when a provider is configured, scores candidate clips, drafts titles/hooks/captions, optionally renders MP4 drafts, and stops at human approval.

Default mode is review-only. It does not publish, upload, or move files into platform handoff folders.

## Repository Name Options

The recommended repository name is `video-review-os`.

Other reasonable names:

- `raw-video-review-os`
- `creator-review-pipeline`
- `local-video-draft-os`
- `clip-review-gate`

## README Outline

- What the tool does
- Install and prerequisites
- Quick start
- Architecture
- Configuration
- Artifact schemas
- CLI commands
- Quality gate model
- Approval workflow
- MVP checklist
- Testing checklist
- Security and privacy notes
- Non-goals

## Install

Prerequisites:

- Python 3.11+
- `ffmpeg` and `ffprobe` on `PATH`
- Optional: `openai-whisper` or `faster-whisper` for local CPU transcription

```bash
git clone https://github.com/YOUR-ORG/video-review-os.git
cd video-review-os
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

For local Whisper:

```bash
python -m pip install -e ".[whisper]"
```

or:

```bash
python -m pip install -e ".[faster-whisper]"
```

## Quick Start

```bash
video-review-os init-config --path config.toml
mkdir -p raw
cp /path/to/video.mp4 raw/
video-review-os --config config.toml run-once
video-review-os --config config.toml dashboard
```

Open `dashboard/index.html` in a browser.

Rendering is explicit:

```bash
video-review-os --config config.toml run-once --render
```

By default, only `keep` clips render. `trim` and `review` stay visible as candidates. `reject` clips never render.

## Architecture Overview

The project is intentionally simple:

- `ingest.py`: discovers or ingests source videos, creates a project folder, runs `ffprobe`, and writes `source.json`.
- `transcribe.py`: writes `transcript.json` through a pluggable provider. It supports deterministic fallback, local Whisper, faster-whisper, and a generic hosted HTTP adapter.
- `quality_gate.py`: scores clips from viewer context, transcript words, timing, and copy quality signals.
- `clip_select.py`: turns transcript words into candidate clip ranges and applies the quality gate.
- `copy.py`: creates title, hook, and caption drafts with deterministic fallback and optional generic hosted copy generation.
- `render.py`: renders MP4 drafts with `ffmpeg`. Default render policy is `keep` only.
- `dashboard.py`: builds static `dashboard.json` and `index.html`.
- `approval.py`: records approval against a stable clip signature.
- `cli.py`: exposes the local commands.

Artifacts are JSON sidecars in each project folder. Source videos are never deleted.

## Config Schema

`config.toml`:

```toml
[paths]
watch_dir = "./raw"
projects_dir = "./projects"
dashboard_dir = "./dashboard"

[media]
ffmpeg_path = "ffmpeg"
ffprobe_path = "ffprobe"
copy_source_to_project = false

[transcription]
provider = "fallback" # fallback, whisper, faster-whisper, generic-http
model = "base"
language = ""
device = "cpu"
compute_type = "int8"
hosted_endpoint_env = "VIDEO_REVIEW_TRANSCRIBE_ENDPOINT"
hosted_api_key_env = "VIDEO_REVIEW_TRANSCRIBE_API_KEY"

[quality_gate]
keep_threshold = 80
trim_threshold = 65
review_threshold = 45
min_clip_seconds = 12.0
ideal_min_seconds = 18.0
max_clip_seconds = 90.0
awkward_pause_seconds = 2.2
opening_word_window = 8

[copy]
provider = "fallback" # fallback, generic-http
hosted_endpoint_env = "VIDEO_REVIEW_COPY_ENDPOINT"
hosted_api_key_env = "VIDEO_REVIEW_COPY_API_KEY"

[render]
video_codec = "libx264"
audio_codec = "aac"
preset = "veryfast"
crf = 23
default_decisions = ["keep"]
```

## Artifact Schema Examples

`source.json`:

```json
{
  "schema_version": "video_review_os.source.v1",
  "project_id": "sample-video-abc123",
  "source": {
    "original_path": "/videos/raw/sample-video.mp4",
    "active_path": "/videos/raw/sample-video.mp4",
    "sha256": "abc123...",
    "filename": "sample-video.mp4"
  },
  "media": {
    "ok": true,
    "duration_seconds": 320.4,
    "video": { "codec": "h264", "width": 1920, "height": 1080 },
    "audio": { "codec": "aac", "sample_rate": 48000, "channels": 2 }
  },
  "safety": {
    "source_deleted": false,
    "source_moved": false,
    "auto_publish_enabled": false
  }
}
```

`clips.json`:

```json
{
  "schema_version": "video_review_os.clips.v1",
  "project_id": "sample-video-abc123",
  "candidates": [
    {
      "clip_id": "clip-001",
      "start": 14.2,
      "end": 42.8,
      "text": "A complete thought from the transcript.",
      "decision": "keep",
      "quality_gate": {
        "score": 88,
        "decision": "keep",
        "render_allowed_default": true,
        "flags": []
      }
    }
  ],
  "selection_policy": {
    "default_render_decisions": ["keep"],
    "reject_never_renders": true,
    "auto_publish_enabled": false
  }
}
```

`approvals.json`:

```json
{
  "schema_version": "video_review_os.approvals.v1",
  "approvals": [
    {
      "approval_key": "stable-hash",
      "clip_id": "clip-001",
      "status": "approved",
      "reviewer": "local-reviewer",
      "publish_allowed": false
    }
  ]
}
```

## CLI Commands

```bash
video-review-os init-config --path config.toml
video-review-os --config config.toml scan
video-review-os --config config.toml ingest raw/video.mp4
video-review-os --config config.toml transcribe projects/sample-video-abc123
video-review-os --config config.toml select-clips projects/sample-video-abc123
video-review-os --config config.toml draft-copy projects/sample-video-abc123
video-review-os --config config.toml render projects/sample-video-abc123
video-review-os --config config.toml render projects/sample-video-abc123 --include trim
video-review-os --config config.toml dashboard
video-review-os --config config.toml approve projects/sample-video-abc123 clip-001 --reviewer "local-reviewer"
video-review-os --config config.toml run-once
video-review-os --config config.toml watch --interval 60
```

## Quality Gate Scoring Model

Each candidate starts at 100 points. The gate subtracts points for audience-visible problems:

- Starts mid-thought or opens with missing context.
- Filler-heavy openings such as repeated filler before the point is clear.
- Stammers, restarts, repeated words, or repeated placeholder language.
- Slate and outtake markers such as `cut five`.
- End time beyond the inspected media duration.
- Awkward silence based on word timestamp gaps.
- Too-short standalone ranges.
- Weak ending words that make the clip feel unfinished.
- Generic or low-value hook/caption language.

Decision thresholds:

- `keep`: score >= 80 with no fatal flags.
- `trim`: score >= 65.
- `review`: score >= 45.
- `reject`: score < 45 or fatal flags.

Fatal flags include outtake markers, incomplete video segments, and ranges below the standalone minimum.

## Approval Workflow

Approval is local state, not publishing permission.

1. The pipeline creates candidates.
2. A reviewer checks the dashboard and rendered drafts.
3. The reviewer approves a clip with the CLI.
4. Approval is stored against a stable signature built from source hash, start time, end time, and transcript text hash.
5. If the clip is regenerated with a different source, range, or transcript text, the old approval does not carry forward.

There is no publishing command in this project.

## MVP Implementation Checklist

- [x] Python package and CLI.
- [x] Configurable paths.
- [x] Safe JSON writes through temp files and rename.
- [x] Source video ingest without deleting source files.
- [x] `ffprobe` media inspection.
- [x] Local transcription adapter interfaces.
- [x] Deterministic transcription fallback.
- [x] Clip candidate generation.
- [x] Audience-first quality gate.
- [x] Copy draft generation with deterministic fallback.
- [x] Render selection policy: `keep` only by default, never `reject`.
- [x] `ffmpeg` rendering.
- [x] Static dashboard JSON and HTML.
- [x] Approval state tied to source and time range.
- [x] Unit tests for quality gates and render selection.

## Testing Checklist

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Manual smoke test:

1. Put one video in `raw/`.
2. Run `video-review-os --config config.toml run-once`.
3. Check the project folder for `source.json`, `transcript.json`, `clips.json`, and `drafts/copy.json`.
4. Run `video-review-os --config config.toml render <project> --dry-run`.
5. Run `video-review-os --config config.toml dashboard`.
6. Confirm `dashboard/index.html` shows candidates and decisions.

## Security and Privacy Notes

- Keep raw videos local unless you explicitly configure a hosted transcription or copy adapter.
- Do not put API keys in `config.toml`; use environment variables.
- Review generated text before using it externally.
- JSON sidecars may contain local file paths and transcript text. Treat project folders as sensitive by default.
- Source videos are never deleted by the tool.
- The dashboard is static and local. It does not require a server.

## Non-Goals

- No auto-publishing by default.
- No hidden uploads.
- No automatic movement into platform handoff folders.
- No platform account automation.
- No claim that a `keep` clip is approved for publication.
- No attempt to replace human editorial review.
- No destructive source video cleanup.
- No private deployment assumptions.
