# Azure Blob Storage Integration Guide for SPAs

This document explains how to securely upload files (such as generated audio or images) to Azure Blob Storage, make them publicly downloadable via SAS tokens, and securely embed them into any Single Page Application (SPA).

---

## 1. Environment Variables & Connection Strings

To securely authenticate with Azure, your backend requires a Connection String. This must never be hardcoded into your source code or committed to GitHub.

### Setup `.env` file
Add your Azure Connection String to your local `.env` file:
```env
CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=<your_account>;AccountKey=<your_key>;EndpointSuffix=core.windows.net"
```
*(Make sure your `.env` is listed in your `.gitignore`)*

### Loading the variable in Python
You can load this variable dynamically in your backend using `os.getenv` or configuration tools like `pydantic-settings`:
```python
import os
from dotenv import load_dotenv

load_dotenv()
CONNECTION_STRING = os.getenv("CONNECTION_STRING")
```

---

## 2. Making Files Publicly Downloadable (SAS Tokens)

By default, Azure Blob containers should be set to **Private**. Do not enable public or anonymous access at the container level. Instead, generate a **Shared Access Signature (SAS)**. A SAS token temporarily grants read-only access to a specific file.

### Required Package
Ensure the Azure SDK is installed:
```bash
pip install azure-storage-blob
```

### Python Implementation
Use the following drop-in snippet to upload file bytes and immediately return a **365-day public read-only URL**:

```python
from datetime import datetime, timedelta, UTC
from pathlib import Path

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas
)

def upload_and_get_sas_url(
    connection_string: str,
    container_name: str,
    blob_name: str,
    file_bytes: bytes,
    mime_type: str = "audio/mpeg"
) -> str:
    """
    Uploads bytes to private Azure Blob Storage and returns a public SAS URL valid for 365 days.
    """
    # 1. Parse connection string for Account credentials
    conn_items = dict(item.split("=", 1) for item in connection_string.split(";") if "=" in item)
    account_name = conn_items["AccountName"]
    account_key = conn_items["AccountKey"]

    # 2. Initialize client
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    
    # 3. Ensure container exists
    container_client = blob_service_client.get_container_client(container=container_name)
    try:
        container_client.create_container()
    except ResourceExistsError:
        pass
        
    # 4. Upload file bytes
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
    blob_client.upload_blob(
        data=file_bytes,
        overwrite=True,
        content_settings=ContentSettings(content_type=mime_type)
    )
    
    # 5. Generate 365-day SAS Token
    now_utc = datetime.now(UTC)
    sas_token = generate_blob_sas(
        account_name=account_name,
        account_key=account_key,
        container_name=container_name,
        blob_name=blob_name,
        permission=BlobSasPermissions(read=True),
        start=now_utc - timedelta(minutes=5),  # Buffer for clock skew
        expiry=now_utc + timedelta(days=365),
        content_disposition=f'attachment; filename="{Path(blob_name).name}"',
        content_type=mime_type,
    )
    
    # 6. Assemble and Return Full Public SAS URL
    base_url = blob_service_client.primary_endpoint.rstrip("/")
    return f"{base_url}/{container_name}/{blob_name}?{sas_token}"
```

---

## 3. Using the SAS URL in any SPA

Because the URL already contains the cryptographic SAS token in its query parameters, the file is treated as a standard public web asset by the browser until the token expires. 

Your SPA (React, Vue, Svelte, vanilla HTML) **does not** need any Azure SDKs or special authorization headers.

### Embedding Audio
```html
<audio controls>
  <source src="https://<account>.blob.core.windows.net/<container>/audio.mp3?<sas_token>" type="audio/mpeg">
  Your browser does not support the audio element.
</audio>
```

### Embedding Images
```html
<img src="https://<account>.blob.core.windows.net/<container>/image.jpg?<sas_token>" alt="Generated Asset" />
```

### Download Button
To force the browser to download the file instead of opening it, you can create a standard anchor link:
```html
<a href="https://<account>.blob.core.windows.net/<container>/audio.mp3?<sas_token>" download>
  Download Audio
</a>
```

### CORS Configuration (Crucial for SPAs)
If your SPA uses JavaScript `fetch()` to read the file (e.g., drawing the image to a `<canvas>` or doing custom audio processing), the browser will trigger a CORS preflight request. 
You **must** configure CORS rules on your Azure Storage Account to allow requests from your SPA's domain:
1. Go to your Storage Account in the Azure Portal.
2. Select **Resource sharing (CORS)** under Settings.
3. Add your SPA's URL (e.g., `https://my-app.com` or `http://localhost:3000`) to **Allowed origins**.
4. Allow `GET` and `OPTIONS` methods.
