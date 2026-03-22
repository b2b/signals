# Azure Blob Storage Instructions

This document explains how audio files should be stored in Azure Blob Storage in the Signals codebase.

Use these instructions when refactoring backend and frontend code so that synthesized audio files are uploaded to Azure Blob Storage and exposed through a signed download URL that remains usable for 365 days from upload time.

## Non-Negotiable Rules

1. Use only `CONNECTION_STRING` from `.env` for Azure authentication.
2. Do not read Azure credentials from markdown files, hardcoded constants, frontend code, or request payloads.
3. Do not rely on Azure account-level public blob access. The storage account may have public access disabled.
4. “Publicly downloadable for 365 days” means: generate a Blob SAS URL with read permission that any browser can open without Azure login until the SAS expiry time.
5. Keep all Pydantic models in [models.py](/Users/oleg/VSCodeProjects/signals/models.py).
6. Follow repo standards from `AGENTS.md`: native Python typing, `Field(..., description=...)` on every model field, UUID strings for IDs, UTC datetimes, pure functions, named arguments, and Pydantic v2 patterns only.

## Current Local-Storage Touchpoints

The current implementation stores audio on disk and serves it through a FastAPI endpoint.

- [signals_service/audio_files.py](/Users/oleg/VSCodeProjects/signals/signals_service/audio_files.py): saves bytes under `generated_audio/...`
- [signals_service/workflow.py](/Users/oleg/VSCodeProjects/signals/signals_service/workflow.py): calls `save_concise_result_audio(...)` and writes local metadata into `research.audio_asset`
- [signals_service/app.py](/Users/oleg/VSCodeProjects/signals/signals_service/app.py): serves `/results/{research_id}/audio`
- [signals_service/prompting.py](/Users/oleg/VSCodeProjects/signals/signals_service/prompting.py): builds the current application audio URL
- [templates/result.html](/Users/oleg/VSCodeProjects/signals/templates/result.html): uses `research.concise_result_audio` as the `<audio>` source
- [models.py](/Users/oleg/VSCodeProjects/signals/models.py): currently describes local filesystem storage in `SignalsSettings` and `ResearchAudioAsset`

## Target Architecture

After the refactor:

1. ElevenLabs still generates `audio_bytes` in memory.
2. Backend uploads those bytes directly to Azure Blob Storage.
3. Blob naming remains deterministic:
   `researches/{research_id}/concise-result-audio.{file_extension}`
4. Backend generates a read-only SAS URL valid for 365 days from blob creation.
5. The SAS URL is stored in `research.concise_result_audio`.
6. The frontend audio player uses that SAS URL directly.
7. The app should no longer depend on local audio files in `generated_audio`.
8. The local file-serving route should be removed or left unused.

## Important Azure Behavior

If the Azure storage account has public access disabled, a plain blob URL will fail with:

`PublicAccessNotPermitted`

That is expected. The correct solution is not to make the container public. The correct solution is to generate a SAS URL with:

- blob-level scope
- read permission only
- expiry = upload time + 365 days

The final URL must look like:

```text
https://<account>.blob.core.windows.net/<container>/<blob>?<sas-token>
```

The query string is required. Without it, the URL is not anonymously downloadable.

## Required Environment Variables

Use the existing `.env` loading pattern from `SignalsSettings`.

Required:

- `CONNECTION_STRING`: Azure Blob Storage connection string

Recommended new settings:

- `azure_blob_audio_container`: container name for stored audio blobs

Suggested default:

- `signals-audio`

If the refactor chooses to avoid a new env var, the container name may be hardcoded in backend code, but a settings field is preferred.

## Required Dependency

Add or keep this dependency in [requirements.txt](/Users/oleg/VSCodeProjects/signals/requirements.txt):

```text
azure-storage-blob
```

## Required Model Changes

Update [models.py](/Users/oleg/VSCodeProjects/signals/models.py) so the data model reflects Azure storage instead of local files.

### `SignalsSettings`

Replace or deprecate `audio_storage_directory` with Azure-oriented settings.

Recommended fields:

```python
azure_blob_connection_string: SecretStr = Field(
    ...,
    description="Azure Blob Storage connection string used for concise-result audio uploads.",
)
azure_blob_audio_container: NonEmptyText = Field(
    default="signals-audio",
    description="Azure Blob Storage container name used for concise-result audio blobs.",
)
```

Important:

- map `azure_blob_connection_string` to the `.env` variable `CONNECTION_STRING`
- do not duplicate the secret into a second env var unless there is a strong reason

### `ResearchAudioAsset`

Refactor storage metadata away from local filesystem semantics.

Recommended fields:

```python
storage_provider: str = Field(
    default="azure_blob_storage",
    description="Storage provider used for persisting the synthesized audio asset.",
)
container_name: str | None = Field(
    default=None,
    description="Azure Blob Storage container name where the synthesized audio asset is stored.",
)
blob_name: str | None = Field(
    default=None,
    description="Blob path of the synthesized audio asset inside the Azure container.",
)
blob_url: str | None = Field(
    default=None,
    description="HTTPS Azure Blob SAS URL that allows direct download of the synthesized audio asset.",
)
sas_expires_at: datetime | None = Field(
    default=None,
    description="Timestamp when the Azure Blob SAS URL for the synthesized audio asset expires (UTC).",
)
```

Recommended removals or deprecations:

- `file_path`

### `Research.concise_result_audio`

Keep this field. Change its meaning from “tokenized application URL” to “Azure Blob SAS URL used directly by the SPA audio player.”

Update its description accordingly.

## Required Backend Refactor

### 1. Replace local audio persistence with Azure upload

Refactor [signals_service/audio_files.py](/Users/oleg/VSCodeProjects/signals/signals_service/audio_files.py).

Current behavior:

- builds local path
- writes bytes to disk
- returns local absolute path

Target behavior:

- build deterministic blob name
- create the container if it does not exist
- upload `audio_bytes` directly to Azure
- set blob content type to the generated MIME type, typically `audio/mpeg`
- generate a read-only SAS URL valid for 365 days
- return Azure storage metadata instead of a local path

Recommended function shape:

```python
async def save_concise_result_audio(
    *,
    settings: SignalsSettings,
    research_id: str,
    audio_bytes: bytes,
    file_extension: str,
    mime_type: str,
) -> AzureAudioBlobSaveResult:
    ...
```

Recommended return model:

```python
class AzureAudioBlobSaveResult(SignalsBaseModel):
    """Result of uploading a concise-result audio asset to Azure Blob Storage."""

    container_name: str = Field(..., description="Azure Blob Storage container name used for the upload.")
    blob_name: str = Field(..., description="Blob path used for the uploaded audio asset.")
    blob_url: str = Field(..., description="Read-only SAS URL for downloading the uploaded audio asset.")
    byte_count: int = Field(..., description="Uploaded audio asset size in bytes.", ge=0)
    created_at: datetime = Field(..., description="Timestamp when the blob upload completed successfully (UTC).")
    sas_expires_at: datetime = Field(..., description="Timestamp when the blob SAS URL expires (UTC).")
```

### 2. Use Azure upload result inside the workflow

Refactor [signals_service/workflow.py](/Users/oleg/VSCodeProjects/signals/signals_service/workflow.py).

Current behavior:

- calls `audio_files.save_concise_result_audio(...)`
- writes `file_path`
- sets `concise_result_audio` using `build_result_audio_url(...)`
- emits local filesystem lifecycle messages

Target behavior:

- call Azure upload logic
- set `audio_asset.storage_provider = "azure_blob_storage"`
- set `audio_asset.container_name`
- set `audio_asset.blob_name`
- set `audio_asset.blob_url`
- set `audio_asset.sas_expires_at`
- set `audio_asset.byte_count`
- set `audio_asset.mime_type`
- set `concise_result_audio = audio_asset.blob_url`
- update lifecycle messages so they mention Azure Blob Storage, not local files
- update failure messages so they mention Azure upload/SAS generation failures

The `reference_id` for the audio completion lifecycle event should be the blob name or full blob URL, not a local file path.

### 3. Stop building app-local audio URLs

Refactor [signals_service/prompting.py](/Users/oleg/VSCodeProjects/signals/signals_service/prompting.py).

Current behavior:

- `build_result_audio_url(...)` builds `/results/{research_id}/audio?token=...`

Target behavior:

- stop using `build_result_audio_url(...)` for audio playback
- either delete it or leave it unused
- keep `build_result_url(...)` for the result page itself

### 4. Remove local file serving for audio

Refactor [signals_service/app.py](/Users/oleg/VSCodeProjects/signals/signals_service/app.py).

Current behavior:

- `/results/{research_id}/audio` validates the app token
- resolves local filesystem path
- returns `FileResponse`

Target behavior:

- remove this endpoint if the frontend fully uses `research.concise_result_audio` as a direct SAS URL

Do not keep local file resolution logic once local storage is removed.

## Required Frontend Behavior

The current template already uses `research.concise_result_audio` directly:

- [templates/result.html](/Users/oleg/VSCodeProjects/signals/templates/result.html)

That means the frontend change should be minimal if `concise_result_audio` becomes a valid Azure SAS URL.

Required behavior:

1. Keep using `research.concise_result_audio` as the `<audio src>`.
2. Do not attempt to append the app token to the Azure URL.
3. Treat the SAS URL as opaque.
4. If the audio expires in the future, the backend must regenerate a fresh SAS URL; frontend should not construct one.

## SAS Generation Requirements

When generating the Azure SAS URL:

1. Use `generate_blob_sas(...)`.
2. Permission must be read-only.
3. Scope must be the specific blob, not the whole account.
4. `expiry` must equal `created_at + timedelta(days=365)`.
5. Include a small negative start offset, for example `created_at - timedelta(minutes=5)`, to avoid clock-skew issues.
6. Set `content_type` to the blob MIME type.
7. Set `content_disposition` to attachment with the final filename.
8. Store the exact resulting URL in MongoDB.

Recommended blob naming:

```text
researches/{research_id}/concise-result-audio.{file_extension}
```

Recommended content settings:

```python
ContentSettings(content_type=mime_type)
```

## Suggested Implementation Pattern

Use Azure SDK objects from `azure.storage.blob`:

```python
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, ContentSettings, generate_blob_sas
```

Recommended flow:

```python
def build_blob_name(*, research_id: str, file_extension: str) -> str:
    return f"researches/{research_id}/concise-result-audio.{file_extension}"


def parse_connection_string_value(*, connection_string: str, key: str) -> str:
    connection_items = dict(
        item.split("=", maxsplit=1)
        for item in connection_string.split(";")
        if "=" in item
    )
    return connection_items[key]


def create_blob_service_client(*, connection_string: str) -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(conn_str=connection_string)


def create_container_if_missing(*, blob_service_client: BlobServiceClient, container_name: str) -> None:
    container_client = blob_service_client.get_container_client(container=container_name)
    try:
        container_client.create_container()
    except ResourceExistsError:
        return


def upload_audio_blob(
    *,
    blob_service_client: BlobServiceClient,
    container_name: str,
    blob_name: str,
    audio_bytes: bytes,
    mime_type: str,
) -> None:
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
    blob_client.upload_blob(
        data=audio_bytes,
        overwrite=True,
        content_settings=ContentSettings(content_type=mime_type),
    )


def build_blob_sas_url(
    *,
    blob_service_client: BlobServiceClient,
    connection_string: str,
    container_name: str,
    blob_name: str,
    mime_type: str,
    created_at: datetime,
) -> tuple[str, datetime]:
    account_name = parse_connection_string_value(connection_string=connection_string, key="AccountName")
    account_key = parse_connection_string_value(connection_string=connection_string, key="AccountKey")
    expires_at = created_at + timedelta(days=365)
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
    base_url = blob_service_client.primary_endpoint.rstrip("/")
    return f"{base_url}/{container_name}/{blob_name}?{sas_token}", expires_at
```

## Error Handling Requirements

The refactor must distinguish between these failures:

1. ElevenLabs audio generation failed
2. Azure container creation failed
3. Azure blob upload failed
4. Azure SAS generation failed
5. Azure URL verification failed

These failures should update `Research.audio_asset.failure`, `failed_at`, `status`, and lifecycle events consistently.

## Verification Requirements

After the refactor, verify all of the following:

1. A completed research document stores `concise_result_audio` as an Azure SAS URL.
2. The stored `audio_asset.storage_provider` is `azure_blob_storage`.
3. The stored `audio_asset.blob_name` matches the deterministic naming convention.
4. Opening the result page still renders the audio player.
5. The browser can play or download the MP3 directly from the SAS URL.
6. The backend no longer depends on `generated_audio/...` for new audio assets.
7. The old `/results/{research_id}/audio` route is either removed or no longer required for normal playback.

## Summary

The intended final design is:

- audio bytes generated in memory
- uploaded directly to Azure Blob Storage using `CONNECTION_STRING` from `.env`
- stored under a deterministic blob path
- exposed via a read-only blob SAS URL valid for 365 days
- saved in MongoDB on `research.concise_result_audio`
- used directly by the frontend audio player
- no local filesystem audio storage
