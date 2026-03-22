from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, ContentSettings, generate_blob_sas

from models import AzureAudioBlobSaveResult, Research, SignalsSettings
from signals_service.prompting import resolve_repository_path


SAS_VALIDITY_DAYS = 365


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(UTC)


def _build_audio_file_path(*, settings: SignalsSettings, research_id: str, file_extension: str) -> Path:
    """Build the legacy local filesystem path for a synthesized concise-result audio file."""
    audio_root_directory = resolve_repository_path(repository_relative_path=settings.audio_storage_directory)
    return audio_root_directory / "researches" / research_id / f"concise-result-audio.{file_extension}"


def resolve_research_audio_file_path(*, settings: SignalsSettings, research: Research) -> Path | None:
    """Resolve a legacy local audio path and ensure it stays inside the configured storage root."""
    if research.audio_asset.file_path is None:
        return None
    configured_root = resolve_repository_path(repository_relative_path=settings.audio_storage_directory).resolve()
    audio_file_path = Path(research.audio_asset.file_path).resolve()
    if not audio_file_path.is_relative_to(configured_root):
        raise ValueError("Persisted concise-result audio file path is outside the configured storage directory.")
    return audio_file_path


def _build_audio_blob_name(*, research_id: str, file_extension: str) -> str:
    """Build the deterministic Azure blob path for a synthesized concise-result audio file."""
    return f"researches/{research_id}/concise-result-audio.{file_extension}"


def _parse_connection_string_value(*, connection_string: str, key: str) -> str:
    """Extract a named value from an Azure connection string."""
    connection_items = dict(
        item.split("=", maxsplit=1)
        for item in connection_string.split(";")
        if "=" in item
    )
    try:
        return connection_items[key]
    except KeyError as error:
        raise ValueError(f"Azure connection string is missing required key: {key}") from error


def _create_blob_service_client(*, settings: SignalsSettings) -> BlobServiceClient:
    """Create the Azure Blob service client from the configured connection string."""
    return BlobServiceClient.from_connection_string(conn_str=settings.connection_string.get_secret_value())


def _create_container_if_missing(*, blob_service_client: BlobServiceClient, container_name: str) -> None:
    """Create the target Azure blob container when it does not already exist."""
    container_client = blob_service_client.get_container_client(container=container_name)
    try:
        container_client.create_container()
    except ResourceExistsError:
        return


def _build_blob_download_url(
    *,
    settings: SignalsSettings,
    blob_service_client: BlobServiceClient,
    container_name: str,
    blob_name: str,
    mime_type: str,
    created_at: datetime,
) -> tuple[str, datetime]:
    """Generate a read-only Azure Blob SAS URL valid for 365 days from upload time."""
    connection_string = settings.connection_string.get_secret_value()
    account_name = _parse_connection_string_value(connection_string=connection_string, key="AccountName")
    account_key = _parse_connection_string_value(connection_string=connection_string, key="AccountKey")
    expires_at = created_at + timedelta(days=SAS_VALIDITY_DAYS)
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        start=created_at - timedelta(minutes=5),
        expiry=expires_at,
        content_disposition=f'attachment; filename="{Path(blob_name).name}"',
        content_type=mime_type,
    )
    primary_endpoint = blob_service_client.primary_endpoint.rstrip("/")
    return f"{primary_endpoint}/{container_name}/{blob_name}?{sas_token}", expires_at


def _upload_audio_bytes_to_azure_sync(
    *,
    settings: SignalsSettings,
    research_id: str,
    audio_bytes: bytes,
    file_extension: str,
    mime_type: str,
) -> AzureAudioBlobSaveResult:
    """Upload concise-result audio bytes to Azure Blob Storage and return the persisted blob metadata."""
    created_at = _utc_now()
    blob_service_client = _create_blob_service_client(settings=settings)
    container_name = settings.azure_blob_audio_container
    blob_name = _build_audio_blob_name(research_id=research_id, file_extension=file_extension)
    _create_container_if_missing(
        blob_service_client=blob_service_client,
        container_name=container_name,
    )
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
    blob_client.upload_blob(
        data=audio_bytes,
        overwrite=True,
        content_settings=ContentSettings(content_type=mime_type),
    )
    blob_url, sas_expires_at = _build_blob_download_url(
        settings=settings,
        blob_service_client=blob_service_client,
        container_name=container_name,
        blob_name=blob_name,
        mime_type=mime_type,
        created_at=created_at,
    )
    return AzureAudioBlobSaveResult(
        container_name=container_name,
        blob_name=blob_name,
        blob_url=blob_url,
        byte_count=len(audio_bytes),
        created_at=created_at,
        sas_expires_at=sas_expires_at,
    )


async def save_concise_result_audio(
    *,
    settings: SignalsSettings,
    research_id: str,
    audio_bytes: bytes,
    file_extension: str,
    mime_type: str,
) -> AzureAudioBlobSaveResult:
    """Upload synthesized concise-result audio bytes to Azure Blob Storage and return blob metadata."""
    return await asyncio.to_thread(
        _upload_audio_bytes_to_azure_sync,
        settings=settings,
        research_id=research_id,
        audio_bytes=audio_bytes,
        file_extension=file_extension,
        mime_type=mime_type,
    )


def _upload_local_audio_file_to_azure_sync(
    *,
    settings: SignalsSettings,
    research_id: str,
    source_file_path: Path,
    file_extension: str,
    mime_type: str,
) -> AzureAudioBlobSaveResult:
    """Upload an existing local concise-result audio file to Azure Blob Storage."""
    audio_bytes = source_file_path.read_bytes()
    return _upload_audio_bytes_to_azure_sync(
        settings=settings,
        research_id=research_id,
        audio_bytes=audio_bytes,
        file_extension=file_extension,
        mime_type=mime_type,
    )


async def migrate_local_audio_file_to_azure(
    *,
    settings: SignalsSettings,
    research_id: str,
    source_file_path: Path,
    file_extension: str,
    mime_type: str,
) -> AzureAudioBlobSaveResult:
    """Upload a legacy local concise-result audio file to Azure Blob Storage."""
    return await asyncio.to_thread(
        _upload_local_audio_file_to_azure_sync,
        settings=settings,
        research_id=research_id,
        source_file_path=source_file_path,
        file_extension=file_extension,
        mime_type=mime_type,
    )
