from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import atomic_write_text


@dataclass(frozen=True)
class PathsConfig:
    watch_dir: Path = Path("raw")
    projects_dir: Path = Path("projects")
    dashboard_dir: Path = Path("dashboard")


@dataclass(frozen=True)
class MediaConfig:
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    copy_source_to_project: bool = False


@dataclass(frozen=True)
class TranscriptionConfig:
    provider: str = "fallback"
    model: str = "base"
    language: str | None = None
    device: str = "cpu"
    compute_type: str = "int8"
    hosted_endpoint_env: str = "VIDEO_REVIEW_TRANSCRIBE_ENDPOINT"
    hosted_api_key_env: str = "VIDEO_REVIEW_TRANSCRIBE_API_KEY"


@dataclass(frozen=True)
class GateConfig:
    keep_threshold: int = 80
    trim_threshold: int = 65
    review_threshold: int = 45
    min_clip_seconds: float = 12.0
    ideal_min_seconds: float = 18.0
    max_clip_seconds: float = 90.0
    awkward_pause_seconds: float = 2.2
    opening_word_window: int = 8


@dataclass(frozen=True)
class CopyConfig:
    provider: str = "fallback"
    hosted_endpoint_env: str = "VIDEO_REVIEW_COPY_ENDPOINT"
    hosted_api_key_env: str = "VIDEO_REVIEW_COPY_API_KEY"


@dataclass(frozen=True)
class CaptionConfig:
    max_chars: int = 42
    max_seconds: float = 3.5
    include_decisions: tuple[str, ...] = ("keep", "trim", "review")


@dataclass(frozen=True)
class SceneConfig:
    frames_per_clip: int = 3
    image_extension: str = "jpg"
    include_decisions: tuple[str, ...] = ("keep", "trim", "review")


@dataclass(frozen=True)
class VisualConfig:
    thumbnail_width: int = 1280
    thumbnail_height: int = 720
    scene_card_width: int = 1080
    scene_card_height: int = 1920
    brand_accent: str = "#2563eb"
    background: str = "#111827"
    text_color: str = "#ffffff"
    mascot_image_path: Path | None = None
    logo_image_path: Path | None = None
    include_decisions: tuple[str, ...] = ("keep", "trim", "review")


@dataclass(frozen=True)
class RenderConfig:
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    preset: str = "veryfast"
    crf: int = 23
    default_decisions: tuple[str, ...] = ("keep",)


@dataclass(frozen=True)
class VideoReviewConfig:
    paths: PathsConfig = field(default_factory=PathsConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    gate: GateConfig = field(default_factory=GateConfig)
    copy: CopyConfig = field(default_factory=CopyConfig)
    captions: CaptionConfig = field(default_factory=CaptionConfig)
    scenes: SceneConfig = field(default_factory=SceneConfig)
    visuals: VisualConfig = field(default_factory=VisualConfig)
    render: RenderConfig = field(default_factory=RenderConfig)

    @classmethod
    def from_file(cls, path: Path | None) -> "VideoReviewConfig":
        if path is None:
            return cls()
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        base = path.parent
        return cls(
            paths=_paths_config(raw.get("paths", {}), base),
            media=_section(MediaConfig, raw.get("media", {})),
            transcription=_section(TranscriptionConfig, raw.get("transcription", {})),
            gate=_section(GateConfig, raw.get("quality_gate", {})),
            copy=_section(CopyConfig, raw.get("copy", {})),
            captions=_caption_config(raw.get("captions", {})),
            scenes=_scene_config(raw.get("scenes", {})),
            visuals=_visual_config(raw.get("visuals", {}), base),
            render=_render_config(raw.get("render", {})),
        )


def _section(cls: type[Any], values: dict[str, Any]) -> Any:
    allowed = cls.__dataclass_fields__.keys()  # type: ignore[attr-defined]
    clean = {key: value for key, value in values.items() if key in allowed}
    return cls(**clean)


def _paths_config(values: dict[str, Any], base: Path) -> PathsConfig:
    def path_value(name: str, default: str) -> Path:
        raw = values.get(name, default)
        path = Path(os.path.expandvars(str(raw))).expanduser()
        return path if path.is_absolute() else base / path

    return PathsConfig(
        watch_dir=path_value("watch_dir", "raw"),
        projects_dir=path_value("projects_dir", "projects"),
        dashboard_dir=path_value("dashboard_dir", "dashboard"),
    )


def _render_config(values: dict[str, Any]) -> RenderConfig:
    defaults = RenderConfig()
    decisions = values.get("default_decisions", list(defaults.default_decisions))
    clean = {key: value for key, value in values.items() if key != "default_decisions"}
    config = _section(RenderConfig, clean)
    return RenderConfig(
        video_codec=config.video_codec,
        audio_codec=config.audio_codec,
        preset=config.preset,
        crf=config.crf,
        default_decisions=tuple(decisions),
    )


def _caption_config(values: dict[str, Any]) -> CaptionConfig:
    defaults = CaptionConfig()
    include_decisions = values.get("include_decisions", list(defaults.include_decisions))
    clean = {key: value for key, value in values.items() if key != "include_decisions"}
    config = _section(CaptionConfig, clean)
    return CaptionConfig(
        max_chars=config.max_chars,
        max_seconds=config.max_seconds,
        include_decisions=tuple(include_decisions),
    )


def _scene_config(values: dict[str, Any]) -> SceneConfig:
    defaults = SceneConfig()
    include_decisions = values.get("include_decisions", list(defaults.include_decisions))
    clean = {key: value for key, value in values.items() if key != "include_decisions"}
    config = _section(SceneConfig, clean)
    return SceneConfig(
        frames_per_clip=config.frames_per_clip,
        image_extension=config.image_extension,
        include_decisions=tuple(include_decisions),
    )


def _visual_config(values: dict[str, Any], base: Path) -> VisualConfig:
    defaults = VisualConfig()
    include_decisions = values.get("include_decisions", list(defaults.include_decisions))
    clean = {
        key: value
        for key, value in values.items()
        if key not in {"include_decisions", "mascot_image_path", "logo_image_path"}
    }
    config = _section(VisualConfig, clean)

    def optional_path(name: str) -> Path | None:
        raw = values.get(name)
        if raw is None or str(raw).strip() == "":
            return None
        path = Path(os.path.expandvars(str(raw))).expanduser()
        return path if path.is_absolute() else base / path

    return VisualConfig(
        thumbnail_width=config.thumbnail_width,
        thumbnail_height=config.thumbnail_height,
        scene_card_width=config.scene_card_width,
        scene_card_height=config.scene_card_height,
        brand_accent=config.brand_accent,
        background=config.background,
        text_color=config.text_color,
        mascot_image_path=optional_path("mascot_image_path"),
        logo_image_path=optional_path("logo_image_path"),
        include_decisions=tuple(include_decisions),
    )


EXAMPLE_CONFIG = """# Video Review OS local config.
# All paths are local. Keep secrets in environment variables, not this file.

[paths]
watch_dir = "./raw"
projects_dir = "./projects"
dashboard_dir = "./dashboard"

[media]
ffmpeg_path = "ffmpeg"
ffprobe_path = "ffprobe"
copy_source_to_project = false

[transcription]
# Options: fallback, whisper, faster-whisper, generic-http
provider = "fallback"
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
# Options: fallback, generic-http
provider = "fallback"
hosted_endpoint_env = "VIDEO_REVIEW_COPY_ENDPOINT"
hosted_api_key_env = "VIDEO_REVIEW_COPY_API_KEY"

[captions]
max_chars = 42
max_seconds = 3.5
include_decisions = ["keep", "trim", "review"]

[scenes]
frames_per_clip = 3
image_extension = "jpg"
include_decisions = ["keep", "trim", "review"]

[visuals]
thumbnail_width = 1280
thumbnail_height = 720
scene_card_width = 1080
scene_card_height = 1920
brand_accent = "#2563eb"
background = "#111827"
text_color = "#ffffff"
# Optional local brand assets. Leave blank for the generic open-source theme.
mascot_image_path = ""
logo_image_path = ""
include_decisions = ["keep", "trim", "review"]

[render]
video_codec = "libx264"
audio_codec = "aac"
preset = "veryfast"
crf = 23
default_decisions = ["keep"]
"""


def write_example_config(path: Path) -> None:
    atomic_write_text(path, EXAMPLE_CONFIG)
