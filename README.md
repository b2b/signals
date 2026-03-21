# signals

Signals is a FastAPI service for whitelisted recent-developments research requests. It validates the requester,
submits a Gemini Deep Research job, polls for completion, summarizes the final report with standard Gemini inference,
and emails a secure deep link to a browser result page.

## What is included

- FastAPI server entrypoint at [`main.py`](/Users/oleg/VSCodeProjects/signals/main.py)
- MongoDB-backed workflow state with Pydantic models in [`models.py`](/Users/oleg/VSCodeProjects/signals/models.py)
- Background poller for Gemini Deep Research interactions
- HTML intake page, result page, and email template under [`templates`](/Users/oleg/VSCodeProjects/signals/templates)
- Local whitelist seeding script at [`mock_data_genetaror.py`](/Users/oleg/VSCodeProjects/signals/mock_data_genetaror.py)

## Local setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy [`.env.example`](/Users/oleg/VSCodeProjects/signals/.env.example) to `.env` and fill in:
   `GEMINI_API_KEY`, `MONGODB_URL`, `DATABASE_NAME`, `AGENTMAIL_API_KEY`, `EMAIL_ADDRESS`.

4. Seed the two requested whitelist entries:

```bash
python mock_data_genetaror.py
```

5. Run the service:

```bash
uvicorn main:app --reload
```

6. Open [http://localhost:8000](http://localhost:8000).

## Workflow

- The intake page accepts `email` and `topic`.
- The server checks `whitelisted_emails` and `whitelisted_email_domains`.
- Accepted requests create a `researches` document and start Gemini Deep Research using
  `deep-research-pro-preview-12-2025`.
- The background loop polls Gemini every `RESEARCH_POLL_INTERVAL_SECONDS`.
- When research completes, the service generates `concise_result` using `gemini-3-pro-preview`.
- The completion email does not include the summary body. It includes a secure deep link to the
  result page at `/results/{research_id}?token=...`.

## API surface

- `GET /` renders the intake page.
- `POST /api/researches` submits a new research request.
- `GET /api/researches/{research_id}` returns workflow status for the SPA.
- `GET /results/{research_id}?token=...` renders the summary result page.
- `GET /api/health` returns a lightweight health payload.
