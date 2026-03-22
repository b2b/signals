from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorDatabase

from models import AudioStatus, Research, SignalsSettings
from signals_service import audio_files
from signals_service.database import create_mongo_client, get_database, get_researches_collection
from signals_service.repositories import replace_research


def _strip_mongo_id(*, document: dict | None) -> dict | None:
    """Remove MongoDB's internal `_id` field before validating against business models."""
    if document is None:
        return None
    normalized_document = dict(document)
    normalized_document.pop("_id", None)
    return normalized_document


def _detect_file_extension(*, source_file_path: Path) -> str:
    """Extract the file extension from a local audio path."""
    file_extension = source_file_path.suffix.lstrip(".")
    if not file_extension:
        raise ValueError(f"Audio file path has no extension: {source_file_path}")
    return file_extension


def _detect_mime_type(*, research: Research, source_file_path: Path) -> str:
    """Choose the best MIME type for a legacy local audio file."""
    if research.audio_asset.mime_type is not None:
        return research.audio_asset.mime_type
    guessed_mime_type, _ = mimetypes.guess_type(source_file_path.name)
    return guessed_mime_type or "audio/mpeg"


async def _list_candidate_researches(*, database: AsyncIOMotorDatabase) -> list[Research]:
    """Load research documents whose completed audio assets still need Azure migration."""
    cursor = (
        get_researches_collection(database=database)
        .find({"audio_asset.status": AudioStatus.COMPLETED.value})
        .sort("created_at", 1)
    )
    return [
        Research.model_validate(_strip_mongo_id(document=document))
        async for document in cursor
    ]


async def _migrate_research_audio(*, database: AsyncIOMotorDatabase, settings: SignalsSettings, research: Research) -> Research:
    """Upload a legacy local audio file to Azure and persist the new blob metadata on the research document."""
    if research.audio_asset.storage_provider == "azure_blob_storage" and research.audio_asset.blob_url is not None:
        return research
    source_file_path = audio_files.resolve_research_audio_file_path(settings=settings, research=research)
    if source_file_path is None:
        raise ValueError(f"Research {research.research_id} does not have a legacy local audio file path.")
    if not source_file_path.exists():
        raise FileNotFoundError(f"Research {research.research_id} audio file does not exist: {source_file_path}")
    file_extension = _detect_file_extension(source_file_path=source_file_path)
    mime_type = _detect_mime_type(research=research, source_file_path=source_file_path)
    stored_audio_asset = await audio_files.migrate_local_audio_file_to_azure(
        settings=settings,
        research_id=research.research_id,
        source_file_path=source_file_path,
        file_extension=file_extension,
        mime_type=mime_type,
    )
    updated_audio_asset = research.audio_asset.model_copy(
        update={
            "storage_provider": stored_audio_asset.storage_provider,
            "container_name": stored_audio_asset.container_name,
            "blob_name": stored_audio_asset.blob_name,
            "blob_url": stored_audio_asset.blob_url,
            "sas_expires_at": stored_audio_asset.sas_expires_at,
            "file_path": None,
            "mime_type": mime_type,
            "byte_count": stored_audio_asset.byte_count,
            "completed_at": research.audio_asset.completed_at or stored_audio_asset.created_at,
            "failed_at": None,
            "failure": None,
            "version": research.audio_asset.version + 1,
        },
        deep=True,
    )
    updated_research = research.model_copy(
        update={
            "concise_result_audio": stored_audio_asset.blob_url,
            "audio_asset": updated_audio_asset,
        },
        deep=True,
    )
    return await replace_research(database=database, research=updated_research)


async def main() -> None:
    """Migrate completed research audio assets from the local filesystem to Azure Blob Storage."""
    settings = SignalsSettings()
    mongo_client = create_mongo_client(mongodb_url=settings.mongodb_url.get_secret_value())
    database = get_database(client=mongo_client, database_name=settings.database_name)
    migrated_count = 0
    skipped_count = 0
    failed_research_ids: list[str] = []
    try:
        researches = await _list_candidate_researches(database=database)
        for research in researches:
            try:
                persisted_research = await _migrate_research_audio(
                    database=database,
                    settings=settings,
                    research=research,
                )
            except Exception as error:
                failed_research_ids.append(research.research_id)
                print(f"FAILED {research.research_id}: {error}")
                continue
            if persisted_research.audio_asset.blob_url == research.audio_asset.blob_url:
                skipped_count += 1
                print(f"SKIPPED {persisted_research.research_id}: already stored in Azure Blob Storage.")
                continue
            migrated_count += 1
            print(f"MIGRATED {persisted_research.research_id}: {persisted_research.concise_result_audio}")
    finally:
        mongo_client.close()
    print(
        "Migration summary: "
        f"migrated={migrated_count} skipped={skipped_count} failed={len(failed_research_ids)}"
    )
    if failed_research_ids:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
