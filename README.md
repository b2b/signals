# Signals

Signals is a FastAPI service for whitelisted recent-developments research requests. Authorized users submit a topic and email address through a single-page app, the service runs a Gemini Deep Research job in the background, generates a concise executive summary, converts that summary to audio with ElevenLabs, uploads the audio to Azure Blob Storage, and emails the requester a secure deep link to their result page.

## What It Does

- Serves a SPA at `/` for topic submission and status polling.
- Validates each requester against MongoDB-backed `whitelisted_emails` and `whitelisted_email_domains` collections.
- Starts Gemini Deep Research with `background=True` using the configured Deep Research agent.
- Polls Gemini every `RESEARCH_POLL_INTERVAL_SECONDS` seconds until the interaction completes.
- Stores the raw research report and citations in MongoDB.
- Summarizes the report with the configured Gemini summary model using the prompt at [`prompts/system_prompt_1.md`](/Users/oleg/VSCodeProjects/signals/prompts/system_prompt_1.md).
- Synthesizes the concise summary with ElevenLabs and uploads the MP3 to Azure Blob Storage.
- Sends a completion email through Agentmail with a token-protected result URL.

## Repository Layout

- [`main.py`](/Users/oleg/VSCodeProjects/signals/main.py): ASGI entrypoint exporting `app`.
- [`signals_service/app.py`](/Users/oleg/VSCodeProjects/signals/signals_service/app.py): FastAPI app factory, lifespan setup, routes, and background polling task.
- [`signals_service/workflow.py`](/Users/oleg/VSCodeProjects/signals/signals_service/workflow.py): End-to-end workflow orchestration.
- [`signals_service/gemini.py`](/Users/oleg/VSCodeProjects/signals/signals_service/gemini.py): Gemini Deep Research submission, polling, and summarization calls.
- [`signals_service/speech.py`](/Users/oleg/VSCodeProjects/signals/signals_service/speech.py): ElevenLabs text-to-speech integration.
- [`signals_service/audio_files.py`](/Users/oleg/VSCodeProjects/signals/signals_service/audio_files.py): Azure Blob Storage upload and SAS URL generation, plus legacy local-file compatibility.
- [`signals_service/emailing.py`](/Users/oleg/VSCodeProjects/signals/signals_service/emailing.py): Agentmail email delivery integration.
- [`signals_service/repositories.py`](/Users/oleg/VSCodeProjects/signals/signals_service/repositories.py): MongoDB read/write helpers.
- [`signals_service/database.py`](/Users/oleg/VSCodeProjects/signals/signals_service/database.py): Motor client setup and MongoDB indexes.
- [`signals_service/rendering.py`](/Users/oleg/VSCodeProjects/signals/signals_service/rendering.py): Markdown and template rendering helpers.
- [`signals_service/prompting.py`](/Users/oleg/VSCodeProjects/signals/signals_service/prompting.py): Prompt loading, hashing, and URL helpers.
- [`models.py`](/Users/oleg/VSCodeProjects/signals/models.py): Consolidated Pydantic models and settings.
- [`templates/`](/Users/oleg/VSCodeProjects/signals/templates): Intake page, result page, error page, and email templates.
- [`static/`](/Users/oleg/VSCodeProjects/signals/static): Frontend assets for the SPA and result views.

## End-to-End Workflow

1. A user submits `topic` and `email` through the SPA at `/`.
2. The server checks the email against `whitelisted_emails` and `whitelisted_email_domains`.
3. If the requester is not authorized, the API returns `403`.
4. If authorized, a `researches` document is created with workflow metadata, a Gemini prompt, and a secure `result_access_token`.
5. The service submits a Gemini Deep Research interaction with `background=True`.
6. A background poller checks Gemini status every `RESEARCH_POLL_INTERVAL_SECONDS` seconds.
7. When Gemini completes, the raw report text and citations are stored in MongoDB.
8. The service generates `concise_result` with the configured Gemini summary model and records prompt metadata for reproducibility.
9. The service synthesizes `concise_result` with ElevenLabs and uploads the resulting MP3 to Azure Blob Storage under a deterministic blob path.
10. A read-only SAS URL is stored on the research document and used for playback.
11. Agentmail sends the requester a completion email with a secure link to `/results/{research_id}?token=...`.
12. The result page renders the concise summary, and audio playback uses the Azure-backed asset URL. The legacy audio endpoint remains for compatibility.

## Local Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy [`.env.example`](/Users/oleg/VSCodeProjects/signals/.env.example) to `.env`.
4. Fill in the required environment variables:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | — | Gemini API key for Deep Research and summarization |
| `ELEVENLABS_API_KEY` | Yes | — | ElevenLabs API key for text-to-speech |
| `MONGODB_URL` | Yes | — | MongoDB Atlas connection string |
| `DATABASE_NAME` | Yes | — | MongoDB database name |
| `AGENTMAIL_API_KEY` | Yes | — | Agentmail API key |
| `EMAIL_ADDRESS` | Yes | — | Sender mailbox used for completion emails |
| `CONNECTION_STRING` | Yes | — | Azure Blob Storage connection string |
| `PUBLIC_BASE_URL` | Yes | `http://localhost:8000` | Base URL used in secure result links |
| `GEMINI_DEEP_RESEARCH_AGENT` | No | `deep-research-pro-preview-12-2025` | Gemini Deep Research agent identifier |
| `GEMINI_SUMMARY_MODEL` | No | `gemini-3-pro-preview` | Gemini model used for concise summary generation |
| `RESEARCH_POLL_INTERVAL_SECONDS` | No | `60` | Background poll interval |
| `SUMMARIZATION_PROMPT_PATH` | No | `prompts/system_prompt_1.md` | Repository-relative summarization system prompt |
| `ELEVENLABS_VOICE_ID` | No | `JBFqnCBsd6RMkjVDRZzb` | ElevenLabs voice ID |
| `ELEVENLABS_MODEL_ID` | No | `eleven_multilingual_v2` | ElevenLabs model ID |
| `ELEVENLABS_OUTPUT_FORMAT` | No | `mp3_44100_128` | ElevenLabs output format |
| `AZURE_BLOB_AUDIO_CONTAINER` | No | `signals-audio` | Azure Blob container for concise-result audio |
| `AUDIO_STORAGE_DIRECTORY` | No | `generated_audio` | Legacy local audio directory retained for compatibility |

5. Insert at least one active whitelist record into MongoDB before testing submissions:
   - `whitelisted_emails` for exact email matches.
   - `whitelisted_email_domains` for domain-based authorization.
6. Start the app:

```bash
uvicorn main:app --reload
```

7. Open [http://localhost:8000](http://localhost:8000).

## API Surface

- `GET /`: renders the SPA intake page.
- `POST /api/researches`: validates whitelist access and creates a new research workflow.
- `GET /api/researches/{research_id}`: returns real-time workflow status for polling.
- `GET /results/{research_id}?token=...`: renders the secure result page.
- `GET /results/{research_id}/audio?token=...`: compatibility endpoint for audio playback; Azure-backed records redirect to the SAS URL.
- `GET /api/health`: returns a lightweight health payload.

## Notes

- MongoDB indexes are ensured at startup by [`signals_service/database.py`](/Users/oleg/VSCodeProjects/signals/signals_service/database.py).
- The summarization prompt is loaded at startup and hashed for reproducibility tracking.
- Azure Blob Storage is the primary audio store. Local file serving only exists for older records during migration/compatibility windows.
