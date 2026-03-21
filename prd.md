# Product Requirements Document (PRD): Signals

## 1. Overview
The Signals service is a specialized tool designed to provide rapid, in-depth research on recent developments in a rapidly changing world. The service allows authorized users (via a Single Page Application) to submit a research topic and their email address. The system validates the user's email against whitelists, orchestrates the Gemini Deep Research Agent to perform the research, processes the final results to generate a concise summary, and emails the findings back to the user.

## 2. Target Audience & Access Control
- **Target Audience:** Corporate and explicitly whitelisted individuals who require rapid, comprehensive research on evolving topics.
- **Access Control:** All requests are strictly gated. The server will only process requests if the user's email is found in one of two MongoDB collections:
  - `whitelisted_emails`: For individual user email addresses.
  - `whitelisted_email_domains`: For corporate users based on email domain (e.g., "hsbc.com").

## 3. Workflow & Features

### 3.1. User Input (SPA)
- **Interface:** A Single Page Application (SPA).
- **Functionality:** 
  - The user inputs a topic they want to research regarding recent developments.
  - The user provides their email address to receive the results.
  - The system will inform the user that they will receive an email upon the completion of the research.

### 3.2. Request Validation
- **Action:** The server receives the topic and user email.
- **Validation:** The server checks the provided email against the `whitelisted_emails` and `whitelisted_email_domains` collections.
- **Outcome:** If the email is not whitelisted, the request is rejected. If accepted, the process proceeds to the next step.

### 3.3. Research Initiation
- **Action:** The server initiates a long-running research task via the **Gemini Deep Research Agent** using the `google-genai` SDK (`client.interactions.create` with `agent='deep-research-pro-preview-12-2025'` and `background=True`).
- **Reference:** 
  - [Gemini Deep Research Agent Docs](https://ai.google.dev/gemini-api/docs/deep-research)
  - [Gemini API Interactions Docs](https://ai.google.dev/gemini-api/docs/interactions)

### 3.4. Background Polling & Storage
- **Mechanism:** A background job executing every 60 seconds.
- **Action:** 
  - The job polls the Gemini Interactions API to check the status of ongoing Deep Researches, using `client.interactions.get(interaction_id)`.
  - It checks if the interaction's `status` transitions from `in_progress` to `completed`.
  - When a research task is `completed`, the server extracts the detailed research text from the final output (`interaction.outputs[-1].text`).
  - If a task is marked as `failed`, the system should log the error and optionally notify the user.
- **Storage:** The raw research result text is stored as a new, separate document in the MongoDB `researches` collection.

### 3.5. Inference & Summarization
- **Trigger:** Executes immediately after a research result is fetched and saved to the database.
- **Action:** The service performs LLM inference against the retrieved research text.
- **Prompt:** Uses the system prompt located at `prompts/system_prompt_1.md`.
- **Storage:** The output of this inference is appended to the corresponding research document in the `researches` collection within a new field named `concise_result`.

### 3.6. Email Notification
- **Action:** The server constructs and sends an email to the user's provided email address.
- **Content:** 
  - A nicely formatted, simple HTML email.
  - An informative message explaining that the research is complete.
  - The embedded summarized text pulled from the `concise_result` field.
- **Integration:** Uses the Agentmail API ([Agentmail Python Docs](https://github.com/agentmail-to/agentmail-python)).

## 4. Technical Specifications & Architecture

### 4.1. Technology Stack
Designed to be consistent with the project's coding principles (`CLAUDE.md`):
- **Language:** Python 3.14
- **API Framework:** FastAPI
- **Database:** MongoDB Atlas (accessed via `motor` for async operations)
- **Data Validation:** Pydantic V2

### 4.2. Database Schema Details (MongoDB)
- **Collections:**
  - `whitelisted_emails`: Identifies specific authorized email addresses.
  - `whitelisted_email_domains`: Identifies authorized corporate domains.
  - `researches`: Stores the asynchronous job outputs.
- **Data Modeling:** All data mappings adhere to existing Pydantic 2+ rules:
  - Utilize `default_factory=lambda: str(uuid4())` for generating business UUIDs.
  - Ensure all internal timezone datetimes resolve to UTC.
  - MongoDB mappings enforce single-query reads where possible without relying on `_id` for business-side interactions.

### 4.3. Environment Variables
The service expects the following secrets in the `.env` file. These must be the exclusive keys leveraged for these platform integrations:
- **Gemini API:**
  - `GEMINI_API_KEY`: Used to authenticate securely with the Gemini Interactions/Deep Research Agent API.
- **MongoDB:**
  - `MONGODB_URL`: Connection string for MongoDB Atlas.
  - `DATABASE_NAME`: Name of the active database.
- **Agentmail API:**
  - `AGENTMAIL_API_KEY`: Authentication key for communicating with the Agentmail outgoing mail service.
  - `EMAIL_ADDRESS`: Represents the originating internal system delivery address.

## 5. Artifacts to Implement next
- `prompts/system_prompt_1.md`: Holds the precise text designed for digesting the long-form research.
- Addition of necessary Pydantic models to `models.py` covering Research workflows.
- API Route for handling incoming SPA requests.
- Asynchronous polling processes acting on the 60-second execution cadence.
