from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from models import SignalsSettings


DEFAULT_AUDIO_FILE_EXTENSION = "mp3"
DEFAULT_AUDIO_MIME_TYPE = "audio/mpeg"


def _import_elevenlabs():
    """Import the ElevenLabs SDK only when concise-result speech generation is needed."""
    from elevenlabs.client import ElevenLabs

    return ElevenLabs


def _normalize_audio_response(*, audio_response: Any) -> bytes:
    """Normalize the ElevenLabs SDK response into a single audio byte string."""
    if isinstance(audio_response, (bytes, bytearray, memoryview)):
        normalized_audio = bytes(audio_response)
        if not normalized_audio:
            raise ValueError("ElevenLabs returned an empty audio payload.")
        return normalized_audio
    if not isinstance(audio_response, Iterable) or isinstance(audio_response, str):
        raise TypeError("ElevenLabs returned an unsupported audio payload type.")
    normalized_chunks = [
        bytes(chunk)
        for chunk in audio_response
        if isinstance(chunk, (bytes, bytearray, memoryview))
    ]
    normalized_audio = b"".join(normalized_chunks)
    if not normalized_audio:
        raise ValueError("ElevenLabs returned no audio bytes.")
    return normalized_audio


def _generate_concise_result_audio_sync(*, settings: SignalsSettings, text: str) -> bytes:
    """Synchronously synthesize concise-result text into speech with ElevenLabs."""
    ElevenLabs = _import_elevenlabs()
    client = ElevenLabs(api_key=settings.elevenlabs_api_key.get_secret_value())
    audio_response = client.text_to_speech.convert(
        text=text,
        voice_id=settings.elevenlabs_voice_id,
        model_id=settings.elevenlabs_model_id,
        output_format=settings.elevenlabs_output_format,
    )
    return _normalize_audio_response(audio_response=audio_response)


async def generate_concise_result_audio(*, settings: SignalsSettings, text: str) -> tuple[bytes, str, str]:
    """Generate an MP3 audio asset for the concise research result."""
    audio_bytes = await asyncio.to_thread(
        _generate_concise_result_audio_sync,
        settings=settings,
        text=text,
    )
    return audio_bytes, DEFAULT_AUDIO_MIME_TYPE, DEFAULT_AUDIO_FILE_EXTENSION
