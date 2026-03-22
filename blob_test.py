from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

import httpx

try:
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.blob import BlobSasPermissions, BlobServiceClient, ContentSettings, generate_blob_sas
except ModuleNotFoundError as error:
    raise SystemExit(
        "Missing dependency 'azure-storage-blob'. Install it with "
        "`python3 -m pip install azure-storage-blob` or `pip install -r requirements.txt`."
    ) from error

from dotenv import load_dotenv


AUDIO_FILE_PATH = (
    Path(__file__).resolve().parent
    / "generated_audio"
    / "researches"
    / "fee86907-39c1-47bd-a10f-9dda95942077"
    / "concise-result-audio.mp3"
)
CONTAINER_NAME = "signals-audio-tests"
BLOB_NAME = "researches/fee86907-39c1-47bd-a10f-9dda95942077/concise-result-audio.mp3"
SAS_VALIDITY_DAYS = 365
OUTPUT_URL_PATH = Path(__file__).resolve().parent / "blob_download_url.txt"


def load_connection_string() -> str:
    """Read the Azure connection string from the local .env file."""
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")
    connection_string = os.getenv("CONNECTION_STRING")
    if not connection_string:
        raise ValueError("Environment variable CONNECTION_STRING is not set in .env.")
    return connection_string


def ensure_upload_file_exists(*, upload_file_path: Path) -> None:
    """Fail fast when the expected local audio file is missing."""
    if not upload_file_path.is_file():
        raise FileNotFoundError(f"Audio file does not exist: {upload_file_path}")


def parse_connection_string_value(*, connection_string: str, key: str) -> str:
    """Extract a named value from the Azure connection string."""
    connection_items = dict(
        item.split("=", maxsplit=1)
        for item in connection_string.split(";")
        if "=" in item
    )
    try:
        return connection_items[key]
    except KeyError as error:
        raise ValueError(f"Azure connection string is missing required key: {key}") from error


def upload_audio_blob(*, connection_string: str, upload_file_path: Path, container_name: str, blob_name: str) -> datetime:
    """Upload the local MP3 to Azure Blob Storage and return the creation timestamp."""
    upload_timestamp = datetime.now(UTC)
    blob_service_client = BlobServiceClient.from_connection_string(conn_str=connection_string)
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
    with upload_file_path.open("rb") as audio_stream:
        blob_client.upload_blob(
            data=audio_stream,
            overwrite=True,
            content_settings=ContentSettings(content_type="audio/mpeg"),
        )

    return upload_timestamp


def create_container_if_missing(*, connection_string: str, container_name: str) -> None:
    """Create the target Azure container when it does not already exist."""
    blob_service_client = BlobServiceClient.from_connection_string(conn_str=connection_string)
    container_client = blob_service_client.get_container_client(container=container_name)
    try:
        container_client.create_container()
    except ResourceExistsError:
        return


def build_download_url(
    *,
    connection_string: str,
    container_name: str,
    blob_name: str,
    account_key: str,
    created_at: datetime,
) -> str:
    """Generate a read-only SAS URL valid for 365 days from upload time."""
    blob_service_client = BlobServiceClient.from_connection_string(conn_str=connection_string)
    account_name = parse_connection_string_value(connection_string=connection_string, key="AccountName")
    primary_endpoint = blob_service_client.primary_endpoint.rstrip("/")
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
        content_type="audio/mpeg",
    )
    return f"{primary_endpoint}/{container_name}/{blob_name}?{sas_token}"


def verify_download_url(*, download_url: str) -> None:
    """Confirm the generated SAS URL can be fetched anonymously over HTTPS."""
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        response = client.get(download_url)
    if response.status_code != 200:
        raise RuntimeError(
            "Generated SAS URL is not downloadable. "
            f"HTTP {response.status_code}: {response.text[:500]}"
        )


def main() -> None:
    """Upload the configured MP3 and print a public download URL with a 365-day SAS token."""
    ensure_upload_file_exists(upload_file_path=AUDIO_FILE_PATH)
    connection_string = load_connection_string()
    account_key = parse_connection_string_value(connection_string=connection_string, key="AccountKey")
    create_container_if_missing(connection_string=connection_string, container_name=CONTAINER_NAME)
    created_at = upload_audio_blob(
        connection_string=connection_string,
        upload_file_path=AUDIO_FILE_PATH,
        container_name=CONTAINER_NAME,
        blob_name=BLOB_NAME,
    )
    download_url = build_download_url(
        connection_string=connection_string,
        container_name=CONTAINER_NAME,
        blob_name=BLOB_NAME,
        account_key=account_key,
        created_at=created_at,
    )
    verify_download_url(download_url=download_url)
    OUTPUT_URL_PATH.write_text(f"{download_url}\n", encoding="utf-8")
    print(download_url)


if __name__ == "__main__":
    main()
