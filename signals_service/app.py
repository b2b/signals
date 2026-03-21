from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from models import (
    ResearchStatusResponse,
    ResearchSubmissionAcceptedResponse,
    ResearchSubmissionRejectedResponse,
    ResearchSubmissionRequest,
    SignalsSettings,
)
from signals_service.audio_files import resolve_research_audio_file_path
from signals_service.database import create_mongo_client, ensure_indexes, get_database
from signals_service.prompting import load_prompt_reference, resolve_repository_path
from signals_service.rendering import render_markdown_to_html
from signals_service.repositories import get_research
from signals_service.workflow import (
    build_submission_response,
    create_research,
    evaluate_whitelist,
    get_status_response,
    run_workflow_cycle,
    submit_queued_research,
)


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


async def _run_polling_loop(*, app: FastAPI) -> None:
    """Run the periodic workflow poller for queued and in-progress research jobs."""
    while True:
        try:
            await run_workflow_cycle(
                database=app.state.database,
                settings=app.state.settings,
                prompt_reference=app.state.prompt_reference,
                prompt_text=app.state.prompt_text,
                template_environment=app.state.templates.env,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Signals background workflow cycle failed.")
        await asyncio.sleep(app.state.settings.research_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the runtime resources required by the Signals service."""
    settings = SignalsSettings()
    mongo_client = create_mongo_client(mongodb_url=settings.mongodb_url.get_secret_value())
    database = get_database(client=mongo_client, database_name=settings.database_name)
    await ensure_indexes(database=database)
    prompt_reference, prompt_text = load_prompt_reference(prompt_path=settings.summarization_prompt_path)
    templates = Jinja2Templates(directory=str(resolve_repository_path(repository_relative_path="templates")))
    app.state.settings = settings
    app.state.mongo_client = mongo_client
    app.state.database = database
    app.state.prompt_reference = prompt_reference
    app.state.prompt_text = prompt_text
    app.state.templates = templates
    app.state.poller_task = asyncio.create_task(_run_polling_loop(app=app))
    try:
        yield
    finally:
        app.state.poller_task.cancel()
        try:
            await app.state.poller_task
        except asyncio.CancelledError:
            pass
        mongo_client.close()


def create_app() -> FastAPI:
    """Create the Signals FastAPI application."""
    app = FastAPI(
        title="Signals",
        description="Deep-research request intake and delivery service for whitelisted users.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(resolve_repository_path(repository_relative_path="static"))),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return app.state.templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "service_name": "Signals",
                "poll_interval_seconds": app.state.settings.research_poll_interval_seconds,
            },
        )

    @app.get("/results/{research_id}", response_class=HTMLResponse)
    async def research_result_page(request: Request, research_id: str, token: str = Query(...)) -> HTMLResponse:
        research = await get_research(database=request.app.state.database, research_id=research_id)
        if research is None or research.result_access_token != token:
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="result_error.html",
                context={
                    "status_code": 404,
                    "title": "Result Not Found",
                    "message": "This research link is invalid or no longer available.",
                },
                status_code=404,
            )
        if research.concise_result is None:
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="result_error.html",
                context={
                    "status_code": 409,
                    "title": "Result Not Ready",
                    "message": "This research has not finished processing yet.",
                },
                status_code=409,
            )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="result.html",
            context={
                "research": research,
                "concise_result_html": render_markdown_to_html(markdown_text=research.concise_result),
                "raw_research_html": render_markdown_to_html(markdown_text=research.raw_research_text) if research.raw_research_text else None,
                "raw_research_citations": research.raw_research_citations,
            },
        )

    @app.get("/results/{research_id}/audio", response_class=FileResponse)
    async def research_result_audio(request: Request, research_id: str, token: str = Query(...)) -> FileResponse:
        research = await get_research(database=request.app.state.database, research_id=research_id)
        if research is None or research.result_access_token != token:
            raise HTTPException(status_code=404, detail="Audio not found.")
        if research.concise_result_audio is None:
            raise HTTPException(status_code=409, detail="Audio is not ready.")
        try:
            audio_file_path = resolve_research_audio_file_path(
                settings=request.app.state.settings,
                research=research,
            )
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if audio_file_path is None or not audio_file_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found.")
        return FileResponse(
            path=audio_file_path,
            media_type=research.audio_asset.mime_type or "audio/mpeg",
            filename=audio_file_path.name,
        )

    @app.get("/api/health")
    async def healthcheck() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "signals",
            "poll_interval_seconds": app.state.settings.research_poll_interval_seconds,
        }

    @app.post(
        "/api/researches",
        response_model=ResearchSubmissionAcceptedResponse,
        status_code=202,
        responses={403: {"model": ResearchSubmissionRejectedResponse}},
    )
    async def submit_research(request: Request, payload: ResearchSubmissionRequest):
        whitelist_decision = await evaluate_whitelist(database=request.app.state.database, email=payload.email)
        if not whitelist_decision.allowed:
            rejected_response = ResearchSubmissionRejectedResponse(
                message="This email address is not currently whitelisted for Signals.",
                email=payload.email,
            )
            return JSONResponse(status_code=403, content=jsonable_encoder(rejected_response))
        research = await create_research(
            database=request.app.state.database,
            topic=payload.topic,
            email=payload.email,
            whitelist_decision=whitelist_decision,
            settings=request.app.state.settings,
            prompt_reference=request.app.state.prompt_reference,
        )
        try:
            research = await submit_queued_research(
                database=request.app.state.database,
                settings=request.app.state.settings,
                research=research,
                raise_on_failure=True,
            )
        except Exception as exc:
            LOGGER.exception("Initial Gemini submission failed for %s.", research.research_id)
            raise HTTPException(
                status_code=502,
                detail=(
                    "Signals accepted the request but could not start the Deep Research job. "
                    f"Research ID: {research.research_id}. Error: {exc}"
                ),
            ) from exc
        return build_submission_response(research=research, settings=request.app.state.settings)

    @app.get("/api/researches/{research_id}", response_model=ResearchStatusResponse)
    async def research_status(request: Request, research_id: str):
        status_response = await get_status_response(database=request.app.state.database, research_id=research_id)
        if status_response is None:
            raise HTTPException(status_code=404, detail="Research not found.")
        return status_response

    return app


app = create_app()
