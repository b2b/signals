from __future__ import annotations

import asyncio
from pathlib import Path

from models import Research, SignalsSettings
from signals_service.prompting import resolve_repository_path


def _build_audio_file_path(*, settings: SignalsSettings, research_id: str, file_extension: str) -> Path:
    """Build the deterministic local filesystem path for a synthesized concise-result audio file."""
    audio_root_directory = resolve_repository_path(repository_relative_path=settings.audio_storage_directory)
    return audio_root_directory / "researches" / research_id / f"concise-result-audio.{file_extension}"


def _write_audio_file_sync(*, settings: SignalsSettings, research_id: str, audio_bytes: bytes, file_extension: str) -> str:
    """Persist synthesized concise-result audio bytes to the local filesystem."""
    audio_file_path = _build_audio_file_path(
        settings=settings,
        research_id=research_id,
        file_extension=file_extension,
    )
    audio_file_path.parent.mkdir(parents=True, exist_ok=True)
    audio_file_path.write_bytes(audio_bytes)
    return str(audio_file_path.resolve())


async def save_concise_result_audio(
    *,
    settings: SignalsSettings,
    research_id: str,
    audio_bytes: bytes,
    file_extension: str,
) -> str:
    """Save synthesized concise-result audio bytes locally and return the absolute file path."""
    return await asyncio.to_thread(
        _write_audio_file_sync,
        settings=settings,
        research_id=research_id,
        audio_bytes=audio_bytes,
        file_extension=file_extension,
    )


def resolve_research_audio_file_path(*, settings: SignalsSettings, research: Research) -> Path | None:
    """Resolve the persisted audio file path for a research document and keep it inside the configured storage root."""
    if research.audio_asset.file_path is None:
        return None
    configured_root = resolve_repository_path(repository_relative_path=settings.audio_storage_directory).resolve()
    audio_file_path = Path(research.audio_asset.file_path).resolve()
    if not audio_file_path.is_relative_to(configured_root):
        raise ValueError("Persisted concise-result audio file path is outside the configured storage directory.")
    return audio_file_path
