from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .config import TranscriptionConfig, VideoReviewConfig
from .utils import atomic_write_json, read_json, utc_now_iso


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, video_path: Path, source: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class FallbackTranscriber(Transcriber):
    def __init__(self, reason: str = "No transcription provider configured") -> None:
        self.reason = reason

    def transcribe(self, video_path: Path, source: dict[str, Any]) -> dict[str, Any]:
        duration = source.get("media", {}).get("duration_seconds") or 0.0
        return transcript_artifact(
            source=source,
            provider="fallback",
            status="fallback",
            segments=[],
            words=[],
            errors=[self.reason],
            metadata={"duration_seconds": duration, "video_path": str(video_path)},
        )


class WhisperTranscriber(Transcriber):
    def __init__(self, config: TranscriptionConfig) -> None:
        self.config = config

    def transcribe(self, video_path: Path, source: dict[str, Any]) -> dict[str, Any]:
        try:
            import whisper  # type: ignore[import-not-found]
        except ImportError:
            return FallbackTranscriber("openai-whisper is not installed").transcribe(video_path, source)

        model = whisper.load_model(self.config.model, device=self.config.device)
        language = self.config.language or None
        raw = model.transcribe(
            str(video_path),
            language=language,
            word_timestamps=True,
            fp16=False,
        )
        return transcript_artifact(
            source=source,
            provider="whisper",
            status="ok",
            segments=_segments_from_whisper(raw.get("segments", [])),
            words=_words_from_whisper(raw.get("segments", [])),
            errors=[],
            metadata={"language": raw.get("language"), "model": self.config.model},
        )


class FasterWhisperTranscriber(Transcriber):
    def __init__(self, config: TranscriptionConfig) -> None:
        self.config = config

    def transcribe(self, video_path: Path, source: dict[str, Any]) -> dict[str, Any]:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError:
            return FallbackTranscriber("faster-whisper is not installed").transcribe(video_path, source)

        model = WhisperModel(
            self.config.model,
            device=self.config.device,
            compute_type=self.config.compute_type,
        )
        language = self.config.language or None
        segments_iter, info = model.transcribe(
            str(video_path),
            language=language,
            word_timestamps=True,
        )
        segments = []
        words = []
        for idx, segment in enumerate(segments_iter):
            segment_words = []
            for word in segment.words or []:
                item = {
                    "word": word.word.strip(),
                    "start": float(word.start),
                    "end": float(word.end),
                    "confidence": None,
                }
                words.append(item)
                segment_words.append(item)
            segments.append(
                {
                    "id": idx,
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": segment.text.strip(),
                    "words": segment_words,
                }
            )
        return transcript_artifact(
            source=source,
            provider="faster-whisper",
            status="ok",
            segments=segments,
            words=words,
            errors=[],
            metadata={"language": getattr(info, "language", None), "model": self.config.model},
        )


class GenericHttpTranscriber(Transcriber):
    def __init__(self, config: TranscriptionConfig) -> None:
        self.config = config

    def transcribe(self, video_path: Path, source: dict[str, Any]) -> dict[str, Any]:
        endpoint = os.getenv(self.config.hosted_endpoint_env, "")
        api_key = os.getenv(self.config.hosted_api_key_env, "")
        if not endpoint:
            return FallbackTranscriber(
                f"Missing hosted transcription endpoint env: {self.config.hosted_endpoint_env}"
            ).transcribe(video_path, source)
        try:
            import requests  # type: ignore[import-not-found]
        except ImportError:
            return FallbackTranscriber("requests is not installed").transcribe(video_path, source)

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        with video_path.open("rb") as handle:
            response = requests.post(
                endpoint,
                headers=headers,
                files={"file": (video_path.name, handle, "application/octet-stream")},
                data={"language": self.config.language or ""},
                timeout=300,
            )
        response.raise_for_status()
        payload = response.json()
        return transcript_artifact(
            source=source,
            provider="generic-http",
            status="ok",
            segments=payload.get("segments", []),
            words=payload.get("words", []),
            errors=[],
            metadata={"endpoint_env": self.config.hosted_endpoint_env},
        )


def transcriber_for(config: TranscriptionConfig) -> Transcriber:
    provider = config.provider.strip().lower()
    if provider in {"", "fallback", "none"}:
        return FallbackTranscriber()
    if provider == "whisper":
        return WhisperTranscriber(config)
    if provider == "faster-whisper":
        return FasterWhisperTranscriber(config)
    if provider == "generic-http":
        return GenericHttpTranscriber(config)
    return FallbackTranscriber(f"Unknown transcription provider: {config.provider}")


def transcribe_project(project_dir: Path, config: VideoReviewConfig) -> Path:
    source = read_json(project_dir / "source.json")
    video_path = Path(source["source"]["active_path"])
    artifact = transcriber_for(config.transcription).transcribe(video_path, source)
    out = project_dir / "transcript.json"
    atomic_write_json(out, artifact)
    return out


def transcript_artifact(
    *,
    source: dict[str, Any],
    provider: str,
    status: str,
    segments: list[dict[str, Any]],
    words: list[dict[str, Any]],
    errors: list[str],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "video_review_os.transcript.v1",
        "created_at": utc_now_iso(),
        "project_id": source["project_id"],
        "source_sha256": source["source"]["sha256"],
        "provider": provider,
        "status": status,
        "segments": segments,
        "words": words,
        "errors": errors,
        "metadata": metadata or {},
    }


def _segments_from_whisper(raw_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": idx,
            "start": float(segment.get("start", 0.0)),
            "end": float(segment.get("end", 0.0)),
            "text": str(segment.get("text", "")).strip(),
            "words": [
                {
                    "word": str(word.get("word", "")).strip(),
                    "start": float(word.get("start", 0.0)),
                    "end": float(word.get("end", 0.0)),
                    "confidence": word.get("probability"),
                }
                for word in segment.get("words", [])
            ],
        }
        for idx, segment in enumerate(raw_segments)
    ]


def _words_from_whisper(raw_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in raw_segments:
        for word in segment.get("words", []):
            words.append(
                {
                    "word": str(word.get("word", "")).strip(),
                    "start": float(word.get("start", 0.0)),
                    "end": float(word.get("end", 0.0)),
                    "confidence": word.get("probability"),
                }
            )
    return words

