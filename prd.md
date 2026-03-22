# Product Requirements Document (PRD): Signals

## 1. Overview
The Signals service is a specialized tool designed to provide rapid, in-depth research on recent developments in a rapidly changing world. The service allows authorized users (via a Single Page Application) to submit a research topic and their email address. The system validates the user's email against whitelists, orchestrates the Gemini Deep Research Agent to perform the research, processes the final results to generate a concise summary and an audio version, and emails the requester a secure deep link to their personalized result page.

## 2. Target Audience & Access Control
- **Target Audience:** Corporate and explicitly whitelisted individuals who require rapid, comprehensive research on evolving topics.
- **Access Control:** All requests are strictly gated. The server will only process requests if the user's email is found in one of two MongoDB collections:
  - `whitelisted_emails`: For individual user email addresses.
  - `whitelisted_email_domains`: For corporate users based on email domain (e.g., `hsbc.com`).

## 3. Workflow & Features

### 3.1. User Input (SPA)
- **Interface:** A Single Page Application (SPA) served at the root path `/`.
- **Functionality:**
  - The user inputs a topic they want to research regarding recent developments.
  - The user provides their email address to receive the results.
  - The system informs the user they will receive a secure email link when research is complete.

### 3.2. Request Validation
- **Action:** The server receives the topic and user email.
- **Validation:** The server checks the provided email against the `whitelisted_emails` and `whitelisted_email_domains` collections.
- **Outcome:** If the email is not whitelisted, the request is rejected with a `403` response. If accepted, the system proceeds and returns a `202` response with the `research_id`.

### 3.3. Research Initiation
- **Action:** The server initiates a long-running research task via the **Gemini Deep Research Agent** using the `google-genai` SDK (`client.interactions.create` with `agent='deep-research-pro-preview-12-2025'` and `background=True`).
- **Prompt construction:** The system builds a structured prompt from the user's topic that instructs Gemini to focus on recent developments, regulatory/financial/competitive shifts, confirmed facts vs. forecasts, and to produce a detailed report suitable for executive summarization.
- **Reference:**
  - [Gemini Deep Research Agent Docs](https://ai.google.dev/gemini-api/docs/deep-research)
  - [Gemini API Interactions Docs](https://ai.google.dev/gemini-api/docs/interactions)

### 3.4. Background Polling & Storage
- **Mechanism:** A background job executing every `RESEARCH_POLL_INTERVAL_SECONDS` seconds (default: 60).
- **Action:**
  - The job polls the Gemini Interactions API to check the status of ongoing Deep Researches, using `client.interactions.get(interaction_id)`.
  - It checks if the interaction's `status` transitions from `in_progress` to `completed`.
  - When a research task is `completed`, the server extracts the detailed research text from the final output (`interaction.outputs[-1].text`) and its source citations.
  - If a task is marked as `failed` or `cancelled`, the system logs the error and marks the workflow accordingly.
- **Storage:** The raw research result text and citations are stored in the MongoDB `researches` collection.

### 3.5. Inference & Summarization
- **Trigger:** Executes immediately after a research result is fetched and saved to the database.
- **Action:** The service invokes Gemini (configured via `GEMINI_SUMMARY_MODEL`) against the retrieved research text.
- **Prompt:** Uses the system prompt loaded from the path specified by `SUMMARIZATION_PROMPT_PATH` (default: `prompts/system_prompt_1.md`). A SHA-256 hash of the prompt is recorded for reproducibility.
- **Storage:** The generated concise summary is stored at `Research.concise_result`. Summary stage metadata (status, model used, character counts, timestamps) is stored in the embedded `ResearchSummary` sub-document.

### 3.6. Audio Generation (ElevenLabs Text-to-Speech)
- **Trigger:** Executes immediately after successful summarization.
- **Action:** The service synthesizes the `concise_result` text into speech using the **ElevenLabs Text-to-Speech API** (`client.text_to_speech.convert`).
- **Configuration:**
  - Voice: configured via `ELEVENLABS_VOICE_ID` (default: `JBFqnCBsd6RMkjVDRZzb`).
  - Model: configured via `ELEVENLABS_MODEL_ID` (default: `eleven_multilingual_v2`).
  - Output format: configured via `ELEVENLABS_OUTPUT_FORMAT` (default: `mp3_44100_128`).
- **Storage:** The generated audio file (MP3) is uploaded directly to Azure Blob Storage using `CONNECTION_STRING`. The blob is stored under a deterministic path, a read-only SAS URL valid for 365 days is generated, and Azure storage metadata is stored in the embedded `ResearchAudioAsset` sub-document. The direct Azure Blob SAS URL is stored at `Research.concise_result_audio`.
- **Reference:** [ElevenLabs TTS API Docs](https://elevenlabs.io/docs/eleven-api/guides/cookbooks/text-to-speech)

### 3.7. Email Notification
- **Trigger:** Executes immediately after audio generation completes successfully.
- **Action:** The server constructs and sends an email to the user's provided email address.
- **Content:**
  - A nicely formatted HTML email (rendered by Jinja2 template).
  - An informative message that research is complete.
  - A **secure deep link** to the result page (`/results/{research_id}?token={result_access_token}`).
- **Integration:** Uses the Agentmail API ([Agentmail Python Docs](https://github.com/agentmail-to/agentmail-python)).

### 3.8. Result Page & Audio Streaming
- **URL:** `/results/{research_id}?token={result_access_token}`
- **Access Control:** Each research document carries an `result_access_token` (opaque UUID). The result page and audio endpoint validate the token before rendering content. Invalid or missing tokens return a 404 error page.
- **Behavior:**
  - If the research has not yet finished processing, a 409 "Result Not Ready" error page is returned.
  - If the research is complete, an HTML result page is rendered showing the `concise_result` as formatted HTML (markdown converted to HTML via the `rendering` module).
- **Audio streaming endpoint:** `GET /results/{research_id}/audio?token={result_access_token}`
  - For Azure-backed records, redirects to the stored Azure Blob SAS URL.
  - For older unmigrated records, may still serve the local MP3 file during the migration window.
  - The frontend can also use `Research.concise_result_audio` directly as the `<audio>` source once it contains the SAS URL.

### 3.9. SPA Status Polling API
- **Endpoint:** `GET /api/researches/{research_id}`
- **Purpose:** Allows the SPA to poll for real-time workflow progress without reloading the page.
- **Response (`ResearchStatusResponse`):**
  - `research_id`, `topic`, `email`, `status`
  - `gemini_status`, `summary_status`, `audio_status`, `notification_status`
  - `concise_result_available` (bool): whether the result is ready to view.
  - `concise_result_audio_available` (bool): whether the audio asset is ready for playback.
  - `created_at`, `updated_at`, `completed_at`, `failed_at`, `latest_failure_message`

## 4. Technical Specifications & Architecture

### 4.1. Technology Stack
- **Language:** Python 3.14
- **API Framework:** FastAPI (with Jinja2 templating and StaticFiles)
- **Database:** MongoDB Atlas (accessed via `motor` for async operations)
- **Data Validation:** Pydantic V2
- **Speech Synthesis:** ElevenLabs Python SDK
- **Email Delivery:** Agentmail Python SDK

### 4.2. Database Schema Details (MongoDB)
- **Collections:**
  - `whitelisted_emails`: Identifies specific authorized email addresses.
  - `whitelisted_email_domains`: Identifies authorized corporate domains.
  - `researches`: Stores the full research workflow document.
- **Data Modelling:** All data mappings adhere to existing Pydantic 2+ rules:
  - UUID business identifiers via `default_factory=lambda: str(uuid4())`.
  - UTC-aware `datetime` for all timestamps.
  - Single-query reads via embedded sub-documents.
  - MongoDB `_id` is never used for business logic.

### 4.3. Environment Variables
All secrets and configuration options are loaded from the `.env` file via `SignalsSettings` (Pydantic Settings):

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | ✓ | — | Authenticates with the Gemini Interactions / Deep Research Agent API |
| `ELEVENLABS_API_KEY` | ✓ | — | Authenticates with the ElevenLabs Text-to-Speech API |
| `MONGODB_URL` | ✓ | — | MongoDB Atlas connection string |
| `DATABASE_NAME` | ✓ | — | MongoDB database name |
| `AGENTMAIL_API_KEY` | ✓ | — | Agentmail outbound email API key |
| `EMAIL_ADDRESS` | ✓ | — | Sender inbox address for completion notifications |
| `PUBLIC_BASE_URL` | ✓ | `http://localhost:8000` | Base URL used to construct deep links in emails |
| `GEMINI_DEEP_RESEARCH_AGENT` | | `deep-research-pro-preview-12-2025` | Gemini agent identifier |
| `GEMINI_SUMMARY_MODEL` | | `gemini-3-pro-preview` | Gemini model used for summarization |
| `RESEARCH_POLL_INTERVAL_SECONDS` | | `60` | Polling cadence for background Gemini status checks |
| `SUMMARIZATION_PROMPT_PATH` | | `prompts/system_prompt_1.md` | Repository-relative path to the summarization system prompt |
| `ELEVENLABS_VOICE_ID` | | `JBFqnCBsd6RMkjVDRZzb` | ElevenLabs voice used for audio synthesis |
| `ELEVENLABS_MODEL_ID` | | `eleven_multilingual_v2` | ElevenLabs model used for audio synthesis |
| `ELEVENLABS_OUTPUT_FORMAT` | | `mp3_44100_128` | Output format for synthesized audio |
| `CONNECTION_STRING` | ✓ | — | Azure Blob Storage connection string used for audio uploads |
| `AZURE_BLOB_AUDIO_CONTAINER` | | `signals-audio` | Azure Blob Storage container name for concise-result audio blobs |
| `AUDIO_STORAGE_DIRECTORY` | | `generated_audio` | Legacy local audio directory retained temporarily for backward compatibility and migration |

## 5. Implemented Modules
- `signals_service/app.py`: FastAPI application factory, lifespan setup, route definitions.
- `signals_service/workflow.py`: Full end-to-end workflow orchestration (whitelist, research creation, Gemini submission/polling, summarization, audio generation, email dispatch).
- `signals_service/gemini.py`: Gemini Deep Research submission and interaction polling logic.
- `signals_service/speech.py`: ElevenLabs TTS synthesis (async wrapper over synchronous SDK call).
- `signals_service/audio_files.py`: Azure Blob Storage persistence and SAS URL generation for synthesized audio files, plus legacy local-file migration helpers.
- `signals_service/emailing.py`: Agentmail send-message integration.
- `signals_service/rendering.py`: Markdown-to-HTML rendering and Jinja2 email template rendering.
- `signals_service/prompting.py`: Prompt loading, SHA-256 hashing, deep link URL construction.
- `signals_service/repositories.py`: MongoDB CRUD operations for researches and whitelist collections.
- `signals_service/database.py`: Motor client factory and MongoDB index management.
- `models.py`: Single consolidated file for all Pydantic models.
- `prompts/system_prompt_1.md`: System prompt for concise-result summarization.
