# Signals Workflow

This document describes the complete end-to-end workflow of the Signals service, step by step, as implemented in `signals_service/workflow.py` and `signals_service/app.py`.

---

## Pipeline Overview

```
SPA → [Submit topic + email]
         ↓
 [1] Whitelist validation
         ↓
 [2] Research document created
         ↓
 [3] Gemini Deep Research submitted (background job)
         ↓
 [4] Background poller checks Gemini status every 60s
         ↓
 [5] Gemini completes → raw report stored
         ↓
 [6] Summarization → concise_result stored
         ↓
 [7] ElevenLabs audio generation → MP3 saved locally
         ↓
 [8] Completion email sent (Agentmail) with secure result link
         ↓
 [9] User opens result page / plays audio
```

---

## Step 0 — Application Startup

**Module:** `signals_service/app.py` (`lifespan`)

1. `SignalsSettings` is loaded from `.env` via Pydantic Settings.
2. A Motor async client is created; MongoDB indexes are ensured via `ensure_indexes`.
3. The summarization prompt is loaded from `SUMMARIZATION_PROMPT_PATH` and its SHA-256 hash is computed (for reproducibility).
4. Jinja2 templates are loaded from the `templates/` directory.
5. A background asyncio task is created to run `_run_polling_loop` — this executes `run_workflow_cycle` once every `RESEARCH_POLL_INTERVAL_SECONDS` seconds (default: 60).

---

## Step 1 — User Submits Research Topic (SPA)

**Endpoint:** `POST /api/researches`  
**Module:** `signals_service/app.py` → `signals_service/workflow.py`

1. The SPA sends a JSON body with `topic` (string, max 2,000 chars) and `email` (valid email address).
2. `ResearchSubmissionRequest` validates and normalizes both fields (email lowercased, topic trimmed).
3. `evaluate_whitelist` is called:
   - Queries `whitelisted_emails` for an exact normalized email match with `status=active`.
   - If not found, queries `whitelisted_email_domains` for a domain match with `status=active`.
   - Returns a `WhitelistDecision`.
4. If **not whitelisted**: returns `ResearchSubmissionRejectedResponse` with HTTP 403.
5. If **whitelisted**: proceeds to Step 2.

---

## Step 2 — Research Document Created

**Module:** `signals_service/workflow.py` (`create_research`, `build_initial_research`)

1. A new `Research` MongoDB document is built with:
   - `research_id` — UUID
   - `result_access_token` — opaque UUID (used to secure the result page)
   - `topic`, `requester` (email + domain snapshot)
   - `whitelist_decision` (embedded, immutable authorization record)
   - `gemini_request` — the structured Gemini prompt built by `build_deep_research_input`
   - `status = research_queued`
   - `summary = ResearchSummary(pending)`, `audio_asset = ResearchAudioAsset(pending)`
   - Initial lifecycle events: `request_accepted`, `whitelist_validated`
2. The document is inserted into the `researches` MongoDB collection.

---

## Step 3 — Gemini Deep Research Submitted

**Module:** `signals_service/workflow.py` (`submit_queued_research`)  
**Provider module:** `signals_service/gemini.py`

1. `submit_queued_research` is called immediately after document creation (during the request) and also by the background poller for any `research_queued` documents it finds.
2. The service calls the Gemini Interactions API with `background=True` and the `deep-research-pro-preview-12-2025` agent.
3. On success:
   - A `GeminiInteractionSnapshot` is embedded into `Research.gemini_interaction`.
   - `Research.status` → `research_in_progress`.
   - Lifecycle event appended: `gemini_submitted`.
4. On failure:
   - `Research.status` → `research_failed`.
   - `ResearchFailure` record appended to `Research.failures`.
   - If the initial submission fails during the POST request, the API returns HTTP 502.

---

## Step 4 — Background Polling Loop

**Module:** `signals_service/app.py` (`_run_polling_loop`)  
**Worker:** `signals_service/workflow.py` (`run_workflow_cycle`)

Every `RESEARCH_POLL_INTERVAL_SECONDS` seconds, `run_workflow_cycle` runs two passes (up to 10 items per pass):

**Pass A — Submit queued jobs:** Finds `Research` documents with `status=research_queued` and attempts Gemini submission (catches any missed submissions from Step 3).

**Pass B — Poll in-progress jobs:** Finds `Research` documents whose `gemini_interaction.next_poll_at` is due and calls `poll_in_progress_research`.

For each in-progress research:
1. Calls `gemini.get_interaction_snapshot(interaction_id)` to get the latest Gemini status.
2. Updates `gemini_interaction` snapshot and appends a `gemini_polled` lifecycle event.
3. Routes to one of the following outcomes based on Gemini status:
   - `in_progress` → update and save, wait for next poll cycle.
   - `completed` → proceed to Step 5.
   - `cancelled` → `Research.status` → `cancelled`, `cancelled_at` set.
   - `failed` / other → `Research.status` → `research_failed`, `ResearchFailure` appended.

---

## Step 5 — Raw Research Stored

**Module:** `signals_service/workflow.py` (inside `poll_in_progress_research`)

When Gemini status is `completed`:

1. `raw_research_text` is extracted from `interaction.outputs[-1].text`.
2. `raw_research_citations` (URL, file, and place citations) are extracted from `final_text_output.annotations`.
3. Both are stored in the `Research` document.
4. `Research.status` → `research_completed`.
5. `raw_research_saved_at` is set.
6. Lifecycle event appended: `research_completed`.
7. Immediately proceeds to Step 6 (no additional polling cycle needed).

---

## Step 6 — Concise Result Generated (Summarization)

**Module:** `signals_service/workflow.py` (`_summarize_completed_research`)  
**Provider:** Gemini (model configured via `GEMINI_SUMMARY_MODEL`)

1. `Research.status` → `summarization_in_progress`.
2. `Research.summary.status` → `in_progress`. Timestamps and model details are set in `ResearchSummary`.
3. Lifecycle event appended: `summarization_started`.
4. The service calls `gemini.generate_concise_result(system_prompt, summary_request)` with the contents of `prompts/system_prompt_1.md` as the system prompt and the raw research text as the user message.
5. On success:
   - `Research.concise_result` is populated with the generated text.
   - `Research.summary.status` → `completed`.
   - `Research.summary.output_character_count` recorded.
   - `Research.status` → `audio_generation_in_progress`.
   - `Research.audio_asset.status` → `in_progress`.
   - Lifecycle events appended: `summarization_completed`, `audio_generation_started`.
   - Immediately proceeds to Step 7.
6. On failure:
   - `Research.summary.status` → `failed`.
   - `Research.status` → `summarization_failed`.
   - `ResearchFailure` appended.

---

## Step 7 — Audio Generation (ElevenLabs TTS)

**Module:** `signals_service/workflow.py` (`_generate_concise_result_audio`)  
**Provider module:** `signals_service/speech.py`  
**Storage module:** `signals_service/audio_files.py`

1. `speech.generate_concise_result_audio` is called with `Research.concise_result` as the input text.
2. The ElevenLabs SDK (`client.text_to_speech.convert`) is invoked synchronously in a thread pool (`asyncio.to_thread`) using:
   - `voice_id` from `ELEVENLABS_VOICE_ID`
   - `model_id` from `ELEVENLABS_MODEL_ID`
   - `output_format` from `ELEVENLABS_OUTPUT_FORMAT` (default: `mp3_44100_128`)
3. The resulting audio bytes are saved to the local filesystem at `{AUDIO_STORAGE_DIRECTORY}/{research_id}.{extension}`.
4. On success:
   - `Research.audio_asset.status` → `completed`.
   - `Research.audio_asset.file_path`, `mime_type`, `byte_count` set.
   - `Research.concise_result_audio` is populated with a tokenized URL: `/results/{research_id}/audio?token={result_access_token}`.
   - `Research.status` → `ready_to_email`.
   - Lifecycle event appended: `audio_generation_completed`.
   - Immediately proceeds to Step 8.
5. On failure (ElevenLabs API error or filesystem error):
   - `Research.audio_asset.status` → `failed`.
   - `Research.status` → `audio_failed`.
   - `ResearchFailure` appended.

---

## Step 8 — Completion Email Sent (Agentmail)

**Module:** `signals_service/workflow.py` (`_send_completion_email`)  
**Provider module:** `signals_service/emailing.py`  
**Template rendering:** `signals_service/rendering.py`

1. `Research.status` → `email_sending`. A `ResearchEmailNotification` (status `in_progress`) is embedded.
2. Lifecycle event appended: `email_started`.
3. `ResearchCompletionEmailContext` is built, including:
   - `result_url`: `{PUBLIC_BASE_URL}/results/{research_id}?token={result_access_token}`
4. Email bodies are rendered by Jinja2 templates:
   - Plain-text body (for email clients that don't render HTML)
   - HTML body (rich format)
5. `AgentmailSendMessageRequest` is sent via the Agentmail Python SDK.
6. On success:
   - `ResearchEmailNotification.status` → `sent`. Provider message and thread IDs stored.
   - `Research.status` → `completed`. `completed_at` set.
   - Lifecycle event appended: `email_sent`.
7. On failure:
   - `ResearchEmailNotification.status` → `failed`.
   - `Research.status` → `email_failed`.
   - `ResearchFailure` appended.

---

## Step 9 — User Opens Result Page

**Endpoints:** `GET /results/{research_id}?token=...` and `GET /results/{research_id}/audio?token=...`  
**Module:** `signals_service/app.py`

### HTML Result Page

1. The `research_id` is looked up in MongoDB.
2. The `token` query parameter is validated against `Research.result_access_token`. On mismatch → 404 error page.
3. If `Research.concise_result` is `None` → 409 "Result Not Ready" error page.
4. Otherwise, the `result.html` Jinja2 template is rendered with:
   - `research` object (for topic, created_at, etc.)
   - `concise_result_html`: the concise result rendered from Markdown to HTML.

### Audio Streaming

1. The same token validation is applied.
2. `Research.concise_result_audio` is checked; if `None` → 409 error.
3. The absolute filesystem path is resolved via `audio_files.resolve_research_audio_file_path`.
4. The MP3 file is served as a `FileResponse` with the correct MIME type.

---

## Step 10 — SPA Status Polling (Optional)

**Endpoint:** `GET /api/researches/{research_id}`  
**Module:** `signals_service/app.py` → `signals_service/workflow.py` (`get_status_response`)

The SPA may poll this endpoint to show real-time progress to the user after submission.

The `ResearchStatusResponse` exposes:

| Field | Description |
|---|---|
| `research_id` | UUID of the workflow |
| `topic` | Original research topic |
| `email` | Requester email |
| `status` | End-to-end `ResearchStatus` |
| `gemini_status` | Latest Gemini interaction status |
| `summary_status` | Summarization stage status |
| `audio_status` | Audio generation stage status |
| `notification_status` | Email delivery status |
| `concise_result_available` | Whether the result is ready to view |
| `concise_result_audio_available` | Whether audio is ready to stream |
| `created_at` / `updated_at` / `completed_at` / `failed_at` | Timestamps |
| `latest_failure_message` | Most recent failure message, if any |

> **Note:** `result_access_token` is **never** included in the status API response. It is only ever embedded in the emailed deep link.

---

## Workflow Status State Machine

```
research_queued
  → research_in_progress           (Gemini submitted)
      → research_completed         (Gemini done; raw report saved)
          → summarization_in_progress
              → audio_generation_in_progress
                  → ready_to_email
                      → email_sending
                          → completed                   ✓ terminal success
                          → email_failed                ✗ terminal failure
                  → audio_failed                        ✗ terminal failure
              → summarization_failed                    ✗ terminal failure
      → research_failed                                 ✗ terminal failure
      → cancelled                                       ✗ terminal cancelled
```

---

## Key Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Gemini API authentication |
| `ELEVENLABS_API_KEY` | — | ElevenLabs TTS authentication |
| `MONGODB_URL` | — | MongoDB Atlas connection |
| `DATABASE_NAME` | — | Database name |
| `AGENTMAIL_API_KEY` | — | Agentmail email delivery |
| `EMAIL_ADDRESS` | — | Sender mailbox |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Base URL for result deep links |
| `GEMINI_DEEP_RESEARCH_AGENT` | `deep-research-pro-preview-12-2025` | Gemini agent |
| `GEMINI_SUMMARY_MODEL` | `gemini-3-pro-preview` | Summarization model |
| `RESEARCH_POLL_INTERVAL_SECONDS` | `60` | Background poll cadence |
| `SUMMARIZATION_PROMPT_PATH` | `prompts/system_prompt_1.md` | System prompt path |
| `ELEVENLABS_VOICE_ID` | `JBFqnCBsd6RMkjVDRZzb` | ElevenLabs voice |
| `ELEVENLABS_MODEL_ID` | `eleven_multilingual_v2` | ElevenLabs model |
| `ELEVENLABS_OUTPUT_FORMAT` | `mp3_44100_128` | Audio output format |
| `AUDIO_STORAGE_DIRECTORY` | `generated_audio` | Local audio file storage |
