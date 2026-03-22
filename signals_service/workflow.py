from __future__ import annotations

import logging
from datetime import UTC, datetime

from jinja2 import Environment
from motor.motor_asyncio import AsyncIOMotorDatabase

from models import (
    AgentmailSendMessageRequest,
    AudioStatus,
    GeminiDeepResearchRequest,
    GeminiInteractionStatus,
    NotificationStatus,
    Research,
    ResearchCompletionEmailContext,
    ResearchEmailNotification,
    ResearchFailure,
    ResearchFailureStage,
    ResearchLifecycleEvent,
    ResearchLifecycleEventType,
    ResearchStatus,
    ResearchStatusResponse,
    ResearchSubmissionAcceptedResponse,
    ResearchSummary,
    ResearchSummaryRequest,
    ResearchRequesterSnapshot,
    SignalsSettings,
    SummaryStatus,
    WhitelistCollectionName,
    WhitelistDecision,
    WhitelistDecisionReason,
    WhitelistMatchType,
)
from signals_service import audio_files, emailing, gemini, speech
from signals_service.prompting import build_deep_research_input, build_result_url
from signals_service.rendering import render_completion_email_bodies
from signals_service.repositories import (
    find_active_whitelisted_email,
    find_active_whitelisted_email_domain,
    get_research,
    insert_research,
    list_queued_researches,
    list_researches_due_for_poll,
    replace_research,
)


LOGGER = logging.getLogger(__name__)
WORKFLOW_BATCH_SIZE = 10
COMPLETION_EMAIL_LABELS = ["signals", "research-ready"]


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def _validated_research_update(*, research: Research, update: dict[str, object]) -> Research:
    """Apply a multi-field update to a research document and validate the merged result atomically."""
    research_payload = research.model_dump()
    research_payload.update(update)
    return Research.model_validate(research_payload)


def append_lifecycle_event(
    *,
    research: Research,
    event_type: ResearchLifecycleEventType,
    message: str,
    reference_id: str | None = None,
) -> Research:
    """Append a workflow lifecycle event to the research document."""
    research.lifecycle_events.append(
        ResearchLifecycleEvent(
            event_type=event_type,
            message=message,
            research_status=research.status,
            gemini_status=research.gemini_interaction.status if research.gemini_interaction else None,
            summary_status=research.summary.status,
            audio_status=research.audio_asset.status,
            notification_status=research.email_notification.status if research.email_notification else None,
            reference_id=reference_id,
        )
    )
    return research


def build_failure(
    *,
    stage: ResearchFailureStage,
    message: str,
    provider: str | None,
    provider_code: str | None,
    retryable: bool,
) -> ResearchFailure:
    """Build a normalized workflow failure record."""
    return ResearchFailure(
        stage=stage,
        message=message,
        provider=provider,
        provider_code=provider_code,
        retryable=retryable,
    )


async def evaluate_whitelist(*, database: AsyncIOMotorDatabase, email: str) -> WhitelistDecision:
    """Evaluate whether the incoming requester is allowed to use the service."""
    requester = ResearchRequesterSnapshot(email=email)
    whitelisted_email = await find_active_whitelisted_email(database=database, email=requester.email)
    if whitelisted_email is not None:
        return WhitelistDecision(
            allowed=True,
            reason=WhitelistDecisionReason.EXACT_EMAIL_MATCH,
            match_type=WhitelistMatchType.EXACT_EMAIL,
            matched_collection_name=WhitelistCollectionName.WHITELISTED_EMAILS,
            matched_entry_id=whitelisted_email.whitelisted_email_id,
            matched_value=whitelisted_email.email,
        )
    whitelisted_domain = await find_active_whitelisted_email_domain(
        database=database,
        domain=requester.email_domain,
    )
    if whitelisted_domain is not None:
        return WhitelistDecision(
            allowed=True,
            reason=WhitelistDecisionReason.DOMAIN_MATCH,
            match_type=WhitelistMatchType.DOMAIN,
            matched_collection_name=WhitelistCollectionName.WHITELISTED_EMAIL_DOMAINS,
            matched_entry_id=whitelisted_domain.whitelisted_email_domain_id,
            matched_value=whitelisted_domain.domain,
        )
    return WhitelistDecision(
        allowed=False,
        reason=WhitelistDecisionReason.NOT_WHITELISTED,
    )


def build_initial_research(
    *,
    topic: str,
    email: str,
    whitelist_decision: WhitelistDecision,
    settings: SignalsSettings,
    prompt_reference,
) -> Research:
    """Create the initial research workflow document for an accepted request."""
    research = Research(
        topic=topic,
        requester=ResearchRequesterSnapshot(email=email),
        whitelist_decision=whitelist_decision,
        status=ResearchStatus.RESEARCH_QUEUED,
        gemini_request=GeminiDeepResearchRequest(
            input=build_deep_research_input(topic=topic),
            agent=settings.gemini_deep_research_agent,
            background=True,
        ),
        summary=ResearchSummary(
            prompt_reference=prompt_reference,
        ),
    )
    append_lifecycle_event(
        research=research,
        event_type=ResearchLifecycleEventType.REQUEST_ACCEPTED,
        message="Research request accepted and queued for Deep Research submission.",
    )
    append_lifecycle_event(
        research=research,
        event_type=ResearchLifecycleEventType.WHITELIST_VALIDATED,
        message=f"Whitelist validation succeeded via {whitelist_decision.reason.value}.",
        reference_id=whitelist_decision.matched_entry_id,
    )
    return research


def build_submission_response(*, research: Research, settings: SignalsSettings) -> ResearchSubmissionAcceptedResponse:
    """Build the API response returned after a research request is accepted."""
    return ResearchSubmissionAcceptedResponse(
        research_id=research.research_id,
        status=research.status,
        message="Your request is accepted. Signals will email you a secure link when the research is ready.",
        email=research.requester.email,
        estimated_poll_interval_seconds=settings.research_poll_interval_seconds,
    )


def build_status_response(*, research: Research) -> ResearchStatusResponse:
    """Build the SPA-facing status payload for a research workflow."""
    latest_failure_message = research.failures[-1].message if research.failures else None
    return ResearchStatusResponse(
        research_id=research.research_id,
        topic=research.topic,
        email=research.requester.email,
        status=research.status,
        gemini_status=research.gemini_interaction.status if research.gemini_interaction else None,
        summary_status=research.summary.status,
        audio_status=research.audio_asset.status,
        notification_status=research.email_notification.status if research.email_notification else None,
        concise_result_available=research.concise_result is not None,
        concise_result_audio_available=research.concise_result_audio is not None,
        created_at=research.created_at,
        updated_at=research.updated_at,
        completed_at=research.completed_at,
        failed_at=research.failed_at,
        latest_failure_message=latest_failure_message,
    )


def _mark_research_failed(
    *,
    research: Research,
    message: str,
    provider_code: str | None,
    retryable: bool,
) -> Research:
    """Move the research workflow into the research-failed terminal state."""
    failure = build_failure(
        stage=ResearchFailureStage.RESEARCH,
        message=message,
        provider="gemini",
        provider_code=provider_code,
        retryable=retryable,
    )
    failed_research = research.model_copy(deep=True)
    failed_research.failures.append(failure)
    failed_research = _validated_research_update(
        research=failed_research,
        update={
            "failures": failed_research.failures,
            "status": ResearchStatus.RESEARCH_FAILED,
            "failed_at": failure.occurred_at,
            "status_updated_at": failure.occurred_at,
        },
    )
    append_lifecycle_event(
        research=failed_research,
        event_type=ResearchLifecycleEventType.RESEARCH_FAILED,
        message=message,
        reference_id=failed_research.gemini_interaction.interaction_id if failed_research.gemini_interaction else None,
    )
    return failed_research


def _mark_summarization_failed(*, research: Research, message: str, provider_code: str | None) -> Research:
    """Move the workflow into the summarization-failed terminal state."""
    failure = build_failure(
        stage=ResearchFailureStage.SUMMARIZATION,
        message=message,
        provider="gemini",
        provider_code=provider_code,
        retryable=False,
    )
    failed_research = research.model_copy(deep=True)
    failed_research.failures.append(failure)
    failed_summary = failed_research.summary.model_copy(
        update={
            "status": SummaryStatus.FAILED,
            "failed_at": failure.occurred_at,
            "failure": failure,
            "version": failed_research.summary.version + 1,
        },
        deep=True,
    )
    failed_research = _validated_research_update(
        research=failed_research,
        update={
            "failures": failed_research.failures,
            "summary": failed_summary,
            "status": ResearchStatus.SUMMARIZATION_FAILED,
            "failed_at": failure.occurred_at,
            "status_updated_at": failure.occurred_at,
        },
    )
    append_lifecycle_event(
        research=failed_research,
        event_type=ResearchLifecycleEventType.SUMMARIZATION_FAILED,
        message=message,
        reference_id=failed_research.research_id,
    )
    return failed_research


def _mark_audio_generation_failed(
    *,
    research: Research,
    message: str,
    provider: str,
    provider_code: str | None,
    retryable: bool,
) -> Research:
    """Move the workflow into the audio-failed terminal state."""
    failure = build_failure(
        stage=ResearchFailureStage.AUDIO,
        message=message,
        provider=provider,
        provider_code=provider_code,
        retryable=retryable,
    )
    failed_research = research.model_copy(deep=True)
    failed_research.failures.append(failure)
    failed_audio_asset = failed_research.audio_asset.model_copy(
        update={
            "status": AudioStatus.FAILED,
            "failed_at": failure.occurred_at,
            "failure": failure,
            "version": failed_research.audio_asset.version + 1,
        },
        deep=True,
    )
    failed_research = _validated_research_update(
        research=failed_research,
        update={
            "failures": failed_research.failures,
            "audio_asset": failed_audio_asset,
            "status": ResearchStatus.AUDIO_FAILED,
            "failed_at": failure.occurred_at,
            "status_updated_at": failure.occurred_at,
        },
    )
    append_lifecycle_event(
        research=failed_research,
        event_type=ResearchLifecycleEventType.AUDIO_GENERATION_FAILED,
        message=message,
        reference_id=failed_research.research_id,
    )
    return failed_research


def _build_email_notification(
    *,
    research: Research,
    settings: SignalsSettings,
    status: NotificationStatus,
    subject: str,
    text_body: str,
    html_body: str,
    requested_at: datetime,
    sent_at: datetime | None = None,
    failure: ResearchFailure | None = None,
    provider_message_id: str | None = None,
    provider_thread_id: str | None = None,
) -> ResearchEmailNotification:
    """Build or update the embedded email notification record."""
    existing_notification = research.email_notification
    version = existing_notification.version + 1 if existing_notification else 1
    base_notification = {
        "from_email": settings.email_address,
        "to_email": research.requester.email,
        "status": status,
        "subject": subject,
        "text_body": text_body,
        "html_body": html_body,
        "labels": COMPLETION_EMAIL_LABELS,
        "requested_at": existing_notification.requested_at if existing_notification else requested_at,
        "sent_at": sent_at,
        "failed_at": failure.occurred_at if failure is not None else None,
        "failure": failure,
        "provider_message_id": provider_message_id,
        "provider_thread_id": provider_thread_id,
        "version": version,
    }
    if existing_notification is None:
        return ResearchEmailNotification(**base_notification)
    return existing_notification.model_copy(update=base_notification, deep=True)


async def create_research(
    *,
    database: AsyncIOMotorDatabase,
    topic: str,
    email: str,
    whitelist_decision: WhitelistDecision,
    settings: SignalsSettings,
    prompt_reference,
) -> Research:
    """Persist the initial research workflow document."""
    research = build_initial_research(
        topic=topic,
        email=email,
        whitelist_decision=whitelist_decision,
        settings=settings,
        prompt_reference=prompt_reference,
    )
    return await insert_research(database=database, research=research)


async def submit_queued_research(
    *,
    database: AsyncIOMotorDatabase,
    settings: SignalsSettings,
    research: Research,
    raise_on_failure: bool = False,
) -> Research:
    """Submit a queued research workflow to Gemini Deep Research."""
    if research.status is not ResearchStatus.RESEARCH_QUEUED:
        return research
    try:
        interaction_snapshot = await gemini.submit_deep_research(
            settings=settings,
            request=research.gemini_request,
        )
    except Exception as exc:
        failed_research = _mark_research_failed(
            research=research,
            message=f"Gemini Deep Research submission failed: {exc}",
            provider_code=None,
            retryable=True,
        )
        persisted_failure = await replace_research(database=database, research=failed_research)
        if raise_on_failure:
            raise
        return persisted_failure
    submitted_research = _validated_research_update(
        research=research,
        update={
            "gemini_interaction": interaction_snapshot,
            "status": ResearchStatus.RESEARCH_IN_PROGRESS,
            "status_updated_at": _utc_now(),
            "failed_at": None,
        },
    )
    append_lifecycle_event(
        research=submitted_research,
        event_type=ResearchLifecycleEventType.GEMINI_SUBMITTED,
        message="Gemini Deep Research interaction submitted successfully.",
        reference_id=interaction_snapshot.interaction_id,
    )
    return await replace_research(database=database, research=submitted_research)


async def _send_completion_email(
    *,
    database: AsyncIOMotorDatabase,
    settings: SignalsSettings,
    template_environment: Environment,
    research: Research,
) -> Research:
    """Send the completion email containing the secure result deep link."""
    if research.concise_result is None:
        raise ValueError("Cannot send a completion email without a concise result.")
    email_context = ResearchCompletionEmailContext(
        research_id=research.research_id,
        topic=research.topic,
        recipient_email=research.requester.email,
        result_url=build_result_url(settings=settings, research=research),
        completed_at=_utc_now(),
    )
    text_body, html_body = render_completion_email_bodies(
        template_environment=template_environment,
        context=email_context,
    )
    subject = f"Signals research ready: {research.topic}"
    email_start_time = _utc_now()
    email_in_progress_notification = _build_email_notification(
        research=research,
        settings=settings,
        status=NotificationStatus.IN_PROGRESS,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        requested_at=email_start_time,
    )
    email_in_progress_research = _validated_research_update(
        research=research,
        update={
            "status": ResearchStatus.EMAIL_SENDING,
            "status_updated_at": email_start_time,
            "email_notification": email_in_progress_notification,
        },
    )
    append_lifecycle_event(
        research=email_in_progress_research,
        event_type=ResearchLifecycleEventType.EMAIL_STARTED,
        message="Completion email send started.",
        reference_id=research.research_id,
    )
    persisted_email_start = await replace_research(database=database, research=email_in_progress_research)
    try:
        send_response = await emailing.send_message(
            settings=settings,
            request=AgentmailSendMessageRequest(
                inbox_id=settings.email_address,
                to=[research.requester.email],
                subject=subject,
                text=text_body,
                html=html_body,
                labels=COMPLETION_EMAIL_LABELS,
            ),
        )
    except Exception as exc:
        failure = build_failure(
            stage=ResearchFailureStage.EMAIL,
            message=f"Completion email send failed: {exc}",
            provider="agentmail",
            provider_code=None,
            retryable=True,
        )
        failed_research = persisted_email_start.model_copy(deep=True)
        failed_research.failures.append(failure)
        failed_notification = _build_email_notification(
            research=failed_research,
            settings=settings,
            status=NotificationStatus.FAILED,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            requested_at=email_start_time,
            failure=failure,
        )
        failed_research = _validated_research_update(
            research=failed_research,
            update={
                "failures": failed_research.failures,
                "status": ResearchStatus.EMAIL_FAILED,
                "failed_at": failure.occurred_at,
                "status_updated_at": failure.occurred_at,
                "email_notification": failed_notification,
            },
        )
        append_lifecycle_event(
            research=failed_research,
            event_type=ResearchLifecycleEventType.EMAIL_FAILED,
            message=failure.message,
            reference_id=failed_research.research_id,
        )
        return await replace_research(database=database, research=failed_research)
    completion_time = _utc_now()
    completed_notification = _build_email_notification(
        research=persisted_email_start,
        settings=settings,
        status=NotificationStatus.SENT,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        requested_at=email_start_time,
        sent_at=completion_time,
        provider_message_id=send_response.message_id,
        provider_thread_id=send_response.thread_id,
    )
    completed_research = _validated_research_update(
        research=persisted_email_start,
        update={
            "status": ResearchStatus.COMPLETED,
            "status_updated_at": completion_time,
            "completed_at": completion_time,
            "email_notification": completed_notification,
        },
    )
    append_lifecycle_event(
        research=completed_research,
        event_type=ResearchLifecycleEventType.EMAIL_SENT,
        message="Completion email sent successfully.",
        reference_id=send_response.message_id,
    )
    return await replace_research(database=database, research=completed_research)


async def _generate_concise_result_audio(
    *,
    database: AsyncIOMotorDatabase,
    settings: SignalsSettings,
    template_environment: Environment,
    research: Research,
) -> Research:
    """Synthesize the concise result into speech, upload it to Azure Blob Storage, then send the completion email."""
    if research.concise_result is None:
        raise ValueError("Cannot synthesize concise-result audio without concise_result.")
    try:
        audio_bytes, mime_type, file_extension = await speech.generate_concise_result_audio(
            settings=settings,
            text=research.concise_result,
        )
    except Exception as exc:
        failed_research = _mark_audio_generation_failed(
            research=research,
            message=f"ElevenLabs audio generation failed: {exc}",
            provider="elevenlabs",
            provider_code=None,
            retryable=True,
        )
        return await replace_research(database=database, research=failed_research)
    try:
        stored_audio_asset = await audio_files.save_concise_result_audio(
            settings=settings,
            research_id=research.research_id,
            audio_bytes=audio_bytes,
            file_extension=file_extension,
            mime_type=mime_type,
        )
    except Exception as exc:
        failed_research = _mark_audio_generation_failed(
            research=research,
            message=f"Azure Blob Storage audio persistence failed: {exc}",
            provider="azure_blob_storage",
            provider_code=None,
            retryable=True,
        )
        return await replace_research(database=database, research=failed_research)
    audio_completion_time = stored_audio_asset.created_at
    completed_audio_asset = research.audio_asset.model_copy(
        update={
            "status": AudioStatus.COMPLETED,
            "storage_provider": stored_audio_asset.storage_provider,
            "container_name": stored_audio_asset.container_name,
            "blob_name": stored_audio_asset.blob_name,
            "blob_url": stored_audio_asset.blob_url,
            "sas_expires_at": stored_audio_asset.sas_expires_at,
            "file_path": None,
            "mime_type": mime_type,
            "byte_count": stored_audio_asset.byte_count,
            "completed_at": audio_completion_time,
            "failed_at": None,
            "failure": None,
            "version": research.audio_asset.version + 1,
        },
        deep=True,
    )
    audio_completed_research = _validated_research_update(
        research=research,
        update={
            "concise_result_audio": stored_audio_asset.blob_url,
            "audio_asset": completed_audio_asset,
            "status": ResearchStatus.READY_TO_EMAIL,
            "status_updated_at": audio_completion_time,
            "failed_at": None,
        },
    )
    append_lifecycle_event(
        research=audio_completed_research,
        event_type=ResearchLifecycleEventType.AUDIO_GENERATION_COMPLETED,
        message="Concise-result audio generated and uploaded to Azure Blob Storage successfully.",
        reference_id=stored_audio_asset.blob_name,
    )
    persisted_audio = await replace_research(database=database, research=audio_completed_research)
    return await _send_completion_email(
        database=database,
        settings=settings,
        template_environment=template_environment,
        research=persisted_audio,
    )


async def _summarize_completed_research(
    *,
    database: AsyncIOMotorDatabase,
    settings: SignalsSettings,
    prompt_reference,
    prompt_text: str,
    template_environment: Environment,
    research: Research,
) -> Research:
    """Run concise-result generation for a completed raw research report, then send the email."""
    if research.raw_research_text is None:
        raise ValueError("Cannot summarize a research workflow without raw research text.")
    summary_start_time = _utc_now()
    started_summary = research.summary.model_copy(
        update={
            "status": SummaryStatus.IN_PROGRESS,
            "started_at": summary_start_time,
            "completed_at": None,
            "failed_at": None,
            "failure": None,
            "model_name": settings.gemini_summary_model,
            "prompt_reference": prompt_reference,
            "input_character_count": len(research.raw_research_text),
            "version": research.summary.version + 1,
        },
        deep=True,
    )
    summarization_started = _validated_research_update(
        research=research,
        update={
            "status": ResearchStatus.SUMMARIZATION_IN_PROGRESS,
            "status_updated_at": summary_start_time,
            "summary": started_summary,
        },
    )
    append_lifecycle_event(
        research=summarization_started,
        event_type=ResearchLifecycleEventType.SUMMARIZATION_STARTED,
        message="Concise-result generation started.",
        reference_id=research.research_id,
    )
    persisted_summary_start = await replace_research(database=database, research=summarization_started)
    try:
        concise_result, model_name = await gemini.generate_concise_result(
            settings=settings,
            system_prompt=prompt_text,
            summary_request=ResearchSummaryRequest(
                research_id=research.research_id,
                topic=research.topic,
                raw_research_text=research.raw_research_text,
                prompt_reference=prompt_reference,
            ),
        )
    except Exception as exc:
        failed_research = _mark_summarization_failed(
            research=persisted_summary_start,
            message=f"Gemini summarization failed: {exc}",
            provider_code=None,
        )
        return await replace_research(database=database, research=failed_research)
    summary_completion_time = _utc_now()
    completed_summary = persisted_summary_start.summary.model_copy(
        update={
            "status": SummaryStatus.COMPLETED,
            "completed_at": summary_completion_time,
            "failed_at": None,
            "failure": None,
            "model_name": model_name,
            "output_character_count": len(concise_result),
            "version": persisted_summary_start.summary.version + 1,
        },
        deep=True,
    )
    started_audio_asset = persisted_summary_start.audio_asset.model_copy(
        update={
            "status": AudioStatus.IN_PROGRESS,
            "model_name": settings.elevenlabs_model_id,
            "voice_id": settings.elevenlabs_voice_id,
            "output_format": settings.elevenlabs_output_format,
            "storage_provider": "azure_blob_storage",
            "container_name": settings.azure_blob_audio_container,
            "blob_name": None,
            "blob_url": None,
            "sas_expires_at": None,
            "file_path": None,
            "mime_type": None,
            "byte_count": 0,
            "started_at": summary_completion_time,
            "completed_at": None,
            "failed_at": None,
            "failure": None,
            "version": persisted_summary_start.audio_asset.version + 1,
        },
        deep=True,
    )
    summarized_research = _validated_research_update(
        research=persisted_summary_start,
        update={
            "concise_result": concise_result,
            "status": ResearchStatus.AUDIO_GENERATION_IN_PROGRESS,
            "status_updated_at": summary_completion_time,
            "failed_at": None,
            "summary": completed_summary,
            "audio_asset": started_audio_asset,
        },
    )
    append_lifecycle_event(
        research=summarized_research,
        event_type=ResearchLifecycleEventType.SUMMARIZATION_COMPLETED,
        message="Concise result generated successfully.",
        reference_id=research.research_id,
    )
    append_lifecycle_event(
        research=summarized_research,
        event_type=ResearchLifecycleEventType.AUDIO_GENERATION_STARTED,
        message="Concise-result audio generation started.",
        reference_id=research.research_id,
    )
    persisted_summary = await replace_research(database=database, research=summarized_research)
    return await _generate_concise_result_audio(
        database=database,
        settings=settings,
        template_environment=template_environment,
        research=persisted_summary,
    )


async def poll_in_progress_research(
    *,
    database: AsyncIOMotorDatabase,
    settings: SignalsSettings,
    prompt_reference,
    prompt_text: str,
    template_environment: Environment,
    research: Research,
) -> Research:
    """Poll Gemini for the latest status of an in-progress research workflow."""
    if research.gemini_interaction is None:
        failed_research = _mark_research_failed(
            research=research,
            message="Research entered the in-progress state without a Gemini interaction snapshot.",
            provider_code=None,
            retryable=False,
        )
        return await replace_research(database=database, research=failed_research)
    try:
        latest_snapshot = await gemini.get_interaction_snapshot(
            settings=settings,
            interaction_id=research.gemini_interaction.interaction_id,
            submitted_at=research.gemini_interaction.submitted_at,
            previous_poll_attempt_count=research.gemini_interaction.poll_attempt_count,
        )
    except Exception as exc:
        failed_research = _mark_research_failed(
            research=research,
            message=f"Gemini interaction polling failed: {exc}",
            provider_code=None,
            retryable=True,
        )
        return await replace_research(database=database, research=failed_research)
    polled_research = _validated_research_update(
        research=research,
        update={
            "gemini_interaction": latest_snapshot,
            "status_updated_at": _utc_now(),
        },
    )
    append_lifecycle_event(
        research=polled_research,
        event_type=ResearchLifecycleEventType.GEMINI_POLLED,
        message=f"Gemini interaction polled with status {latest_snapshot.status.value}.",
        reference_id=latest_snapshot.interaction_id,
    )
    if latest_snapshot.status is GeminiInteractionStatus.IN_PROGRESS:
        return await replace_research(database=database, research=polled_research)
    if latest_snapshot.status is GeminiInteractionStatus.COMPLETED:
        if latest_snapshot.final_text_output is None:
            failed_research = _mark_research_failed(
                research=polled_research,
                message="Gemini marked the research as completed but returned no final text output.",
                provider_code=None,
                retryable=False,
            )
            return await replace_research(database=database, research=failed_research)
        completion_time = _utc_now()
        completed_research = _validated_research_update(
            research=polled_research,
            update={
                "raw_research_text": latest_snapshot.final_text_output.text,
                "raw_research_saved_at": completion_time,
                "raw_research_citations": latest_snapshot.final_text_output.annotations,
                "status": ResearchStatus.RESEARCH_COMPLETED,
                "status_updated_at": completion_time,
            },
        )
        append_lifecycle_event(
            research=completed_research,
            event_type=ResearchLifecycleEventType.RESEARCH_COMPLETED,
            message="Raw research report saved from the completed Gemini interaction.",
            reference_id=latest_snapshot.interaction_id,
        )
        persisted_completion = await replace_research(database=database, research=completed_research)
        return await _summarize_completed_research(
            database=database,
            settings=settings,
            prompt_reference=prompt_reference,
            prompt_text=prompt_text,
            template_environment=template_environment,
            research=persisted_completion,
        )
    if latest_snapshot.status is GeminiInteractionStatus.CANCELLED:
        cancellation_time = _utc_now()
        cancelled_research = _validated_research_update(
            research=polled_research,
            update={
                "status": ResearchStatus.CANCELLED,
                "cancelled_at": cancellation_time,
                "status_updated_at": cancellation_time,
            },
        )
        append_lifecycle_event(
            research=cancelled_research,
            event_type=ResearchLifecycleEventType.STATUS_CHANGED,
            message="Gemini cancelled the interaction before completion.",
            reference_id=latest_snapshot.interaction_id,
        )
        return await replace_research(database=database, research=cancelled_research)
    provider_code = latest_snapshot.error.code if latest_snapshot.error else latest_snapshot.status.value
    provider_message = latest_snapshot.error.message if latest_snapshot.error else None
    failure_message = provider_message or f"Gemini interaction ended with unsupported status {latest_snapshot.status.value}."
    failed_research = _mark_research_failed(
        research=polled_research,
        message=failure_message,
        provider_code=provider_code,
        retryable=False,
    )
    return await replace_research(database=database, research=failed_research)


async def run_workflow_cycle(
    *,
    database: AsyncIOMotorDatabase,
    settings: SignalsSettings,
    prompt_reference,
    prompt_text: str,
    template_environment: Environment,
) -> None:
    """Process queued submissions and poll active Gemini interactions once."""
    queued_researches = await list_queued_researches(database=database, limit=WORKFLOW_BATCH_SIZE)
    for research in queued_researches:
        try:
            await submit_queued_research(
                database=database,
                settings=settings,
                research=research,
            )
        except Exception:
            LOGGER.exception("Queued research submission failed for %s.", research.research_id)
    due_researches = await list_researches_due_for_poll(
        database=database,
        now=_utc_now(),
        limit=WORKFLOW_BATCH_SIZE,
    )
    for research in due_researches:
        try:
            await poll_in_progress_research(
                database=database,
                settings=settings,
                prompt_reference=prompt_reference,
                prompt_text=prompt_text,
                template_environment=template_environment,
                research=research,
            )
        except Exception:
            LOGGER.exception("Workflow polling failed for %s.", research.research_id)


async def get_status_response(*, database: AsyncIOMotorDatabase, research_id: str) -> ResearchStatusResponse | None:
    """Load a research workflow and convert it into the SPA-facing status response."""
    research = await get_research(database=database, research_id=research_id)
    if research is None:
        return None
    return build_status_response(research=research)
