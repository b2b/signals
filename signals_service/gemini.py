from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from models import (
    GeminiDeepResearchRequest,
    GeminiFileCitation,
    GeminiInteractionError,
    GeminiInteractionSnapshot,
    GeminiInteractionStatus,
    GeminiInteractionUsage,
    GeminiPlaceCitation,
    GeminiPlaceReviewSnippet,
    GeminiTextAnnotation,
    GeminiTextOutput,
    GeminiUrlCitation,
    GeminiUsageByModality,
    ResearchSummaryRequest,
    SignalsSettings,
)
from signals_service.prompting import build_summary_contents


def _read_provider_value(*, source: Any, field_names: tuple[str, ...]) -> Any:
    """Read a field from either an SDK object or a plain mapping."""
    for field_name in field_names:
        if isinstance(source, dict) and field_name in source:
            return source[field_name]
        if hasattr(source, field_name):
            return getattr(source, field_name)
    return None


def _normalize_provider_list(*, value: Any) -> list[Any]:
    """Normalize provider collections into a Python list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _parse_provider_datetime(*, value: Any) -> datetime | None:
    """Parse datetimes returned by provider SDKs or APIs into UTC-aware values."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        normalized_value = value.replace("Z", "+00:00")
        try:
            parsed_value = datetime.fromisoformat(normalized_value)
        except ValueError:
            return None
        return parsed_value.astimezone(UTC) if parsed_value.tzinfo else parsed_value.replace(tzinfo=UTC)
    return None


def _coerce_non_negative_int(*, value: Any) -> int | None:
    """Coerce provider numeric fields into non-negative integers when possible."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def _extract_usage_by_modality(*, value: Any) -> list[GeminiUsageByModality]:
    """Extract modality-grouped token counts from flexible provider payloads."""
    normalized_items = _normalize_provider_list(value=value)
    usage_entries: list[GeminiUsageByModality] = []
    for item in normalized_items:
        modality = _read_provider_value(source=item, field_names=("modality", "type", "name"))
        tokens = _read_provider_value(source=item, field_names=("tokens", "token_count", "count"))
        normalized_tokens = _coerce_non_negative_int(value=tokens)
        if not isinstance(modality, str) or normalized_tokens is None:
            continue
        try:
            usage_entries.append(GeminiUsageByModality(modality=modality, tokens=normalized_tokens))
        except ValueError:
            continue
    return usage_entries


def _extract_usage(*, interaction: Any) -> GeminiInteractionUsage | None:
    """Extract the Gemini usage object into the persisted internal representation."""
    usage = _read_provider_value(source=interaction, field_names=("usage",))
    if usage is None:
        return None
    return GeminiInteractionUsage(
        input_tokens_by_modality=_extract_usage_by_modality(
            value=_read_provider_value(source=usage, field_names=("input_tokens_by_modality", "input_tokens_details"))
        ),
        tool_use_tokens_by_modality=_extract_usage_by_modality(
            value=_read_provider_value(source=usage, field_names=("tool_use_tokens_by_modality", "tool_use_tokens_details"))
        ),
        total_cached_tokens=_coerce_non_negative_int(
            value=_read_provider_value(source=usage, field_names=("total_cached_tokens", "cached_content_token_count"))
        ),
        total_input_tokens=_coerce_non_negative_int(
            value=_read_provider_value(source=usage, field_names=("total_input_tokens", "prompt_token_count"))
        ),
        total_output_tokens=_coerce_non_negative_int(
            value=_read_provider_value(source=usage, field_names=("total_output_tokens", "candidates_token_count"))
        ),
        total_reasoning_tokens=_coerce_non_negative_int(
            value=_read_provider_value(source=usage, field_names=("total_reasoning_tokens", "thoughts_token_count"))
        ),
        total_tool_use_tokens=_coerce_non_negative_int(
            value=_read_provider_value(source=usage, field_names=("total_tool_use_tokens", "tool_use_prompt_token_count"))
        ),
        total_tokens=_coerce_non_negative_int(
            value=_read_provider_value(source=usage, field_names=("total_tokens", "total_token_count"))
        ),
    )


def _extract_error(*, interaction: Any) -> GeminiInteractionError | None:
    """Extract a Gemini provider error payload, when present."""
    error = _read_provider_value(source=interaction, field_names=("error",))
    if error is None:
        return None
    return GeminiInteractionError(
        code=_read_provider_value(source=error, field_names=("code", "status")),
        message=_read_provider_value(source=error, field_names=("message", "detail")),
    )


def _extract_annotation(*, annotation: Any) -> GeminiTextAnnotation | None:
    """Normalize a single Gemini text annotation into a typed Pydantic model."""
    annotation_type = _read_provider_value(source=annotation, field_names=("type",))
    common_fields = {
        "start_index": _coerce_non_negative_int(
            value=_read_provider_value(source=annotation, field_names=("start_index", "startIndex"))
        ),
        "end_index": _coerce_non_negative_int(
            value=_read_provider_value(source=annotation, field_names=("end_index", "endIndex"))
        ),
    }
    try:
        if annotation_type == "url_citation":
            return GeminiUrlCitation(
                url=_read_provider_value(source=annotation, field_names=("url", "uri")),
                title=_read_provider_value(source=annotation, field_names=("title",)),
                **common_fields,
            )
        if annotation_type == "file_citation":
            return GeminiFileCitation(
                document_uri=_read_provider_value(source=annotation, field_names=("document_uri", "documentUri", "uri")),
                file_name=_read_provider_value(source=annotation, field_names=("file_name", "fileName", "name")),
                source=_read_provider_value(source=annotation, field_names=("source",)),
                **common_fields,
            )
        if annotation_type == "place_citation":
            review_snippet_value = _read_provider_value(source=annotation, field_names=("review_snippets", "reviewSnippets"))
            review_snippet_items = _normalize_provider_list(value=review_snippet_value)
            normalized_review_snippet = None
            if review_snippet_items:
                normalized_review_snippet = GeminiPlaceReviewSnippet(
                    title=_read_provider_value(source=review_snippet_items[0], field_names=("title",)),
                    url=_read_provider_value(source=review_snippet_items[0], field_names=("url",)),
                    review_id=_read_provider_value(source=review_snippet_items[0], field_names=("review_id", "reviewId")),
                )
            return GeminiPlaceCitation(
                place_id=_read_provider_value(source=annotation, field_names=("place_id", "placeId")),
                name=_read_provider_value(source=annotation, field_names=("name", "title")),
                url=_read_provider_value(source=annotation, field_names=("url", "uri")),
                review_snippets=normalized_review_snippet,
                **common_fields,
            )
    except ValueError:
        return None
    return None


def _extract_final_text_output(*, interaction: Any) -> GeminiTextOutput | None:
    """Extract the final text output block from a Gemini interaction."""
    outputs = _normalize_provider_list(value=_read_provider_value(source=interaction, field_names=("outputs",)))
    for output in reversed(outputs):
        output_text = _read_provider_value(source=output, field_names=("text",))
        output_type = _read_provider_value(source=output, field_names=("type",))
        if output_type not in {None, "text"}:
            continue
        if not isinstance(output_text, str) or not output_text.strip():
            continue
        annotations = [
            normalized_annotation
            for annotation in _normalize_provider_list(value=_read_provider_value(source=output, field_names=("annotations",)))
            if (normalized_annotation := _extract_annotation(annotation=annotation)) is not None
        ]
        return GeminiTextOutput(text=output_text.strip(), annotations=annotations)
    return None


def _build_interaction_snapshot(
    *,
    interaction: Any,
    poll_interval_seconds: int,
    submitted_at: datetime,
    poll_attempt_count: int,
) -> GeminiInteractionSnapshot:
    """Build the persisted Gemini interaction snapshot from a provider interaction object."""
    now = datetime.now(UTC)
    raw_status = _read_provider_value(source=interaction, field_names=("status",))
    raw_status_value = raw_status.value if isinstance(raw_status, GeminiInteractionStatus) else raw_status
    normalized_status = GeminiInteractionStatus(raw_status_value or GeminiInteractionStatus.IN_PROGRESS.value)
    terminal_next_poll_at = None
    if normalized_status in {GeminiInteractionStatus.IN_PROGRESS, GeminiInteractionStatus.REQUIRES_ACTION}:
        terminal_next_poll_at = now + timedelta(seconds=poll_interval_seconds)
    return GeminiInteractionSnapshot(
        interaction_id=_read_provider_value(source=interaction, field_names=("id", "interaction_id")),
        agent_name=_read_provider_value(source=interaction, field_names=("agent", "agent_name")),
        model_name=_read_provider_value(source=interaction, field_names=("model", "model_name")),
        status=normalized_status,
        previous_interaction_id=_read_provider_value(
            source=interaction,
            field_names=("previous_interaction_id", "previousInteractionId"),
        ),
        final_text_output=_extract_final_text_output(interaction=interaction),
        usage=_extract_usage(interaction=interaction),
        error=_extract_error(interaction=interaction),
        created_at=_parse_provider_datetime(value=_read_provider_value(source=interaction, field_names=("created_at", "createdAt"))),
        updated_at=_parse_provider_datetime(value=_read_provider_value(source=interaction, field_names=("updated_at", "updatedAt"))),
        submitted_at=submitted_at,
        last_polled_at=now if poll_attempt_count > 0 else None,
        next_poll_at=terminal_next_poll_at,
        poll_interval_seconds=poll_interval_seconds,
        poll_attempt_count=poll_attempt_count,
        completed_at=now if normalized_status is GeminiInteractionStatus.COMPLETED else None,
        failed_at=now if normalized_status in {GeminiInteractionStatus.FAILED, GeminiInteractionStatus.INCOMPLETE} else None,
        cancelled_at=now if normalized_status is GeminiInteractionStatus.CANCELLED else None,
    )


def _import_genai_modules():
    """Import google-genai modules only when they are needed at runtime."""
    from google import genai
    from google.genai import types

    return genai, types


def _submit_deep_research_sync(*, settings: SignalsSettings, request: GeminiDeepResearchRequest) -> Any:
    """Synchronously call the Gemini Interactions API to create a Deep Research task."""
    genai, _ = _import_genai_modules()
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
    return client.interactions.create(
        input=request.input,
        agent=request.agent.value,
        background=True,
        store=True,
    )


async def submit_deep_research(
    *,
    settings: SignalsSettings,
    request: GeminiDeepResearchRequest,
) -> GeminiInteractionSnapshot:
    """Create a background Gemini Deep Research interaction and normalize the returned snapshot."""
    submitted_at = datetime.now(UTC)
    interaction = await asyncio.to_thread(_submit_deep_research_sync, settings=settings, request=request)
    return _build_interaction_snapshot(
        interaction=interaction,
        poll_interval_seconds=settings.research_poll_interval_seconds,
        submitted_at=submitted_at,
        poll_attempt_count=0,
    )


def _get_interaction_sync(*, settings: SignalsSettings, interaction_id: str) -> Any:
    """Synchronously retrieve a Gemini interaction by its identifier."""
    genai, _ = _import_genai_modules()
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
    return client.interactions.get(interaction_id)


async def get_interaction_snapshot(
    *,
    settings: SignalsSettings,
    interaction_id: str,
    submitted_at: datetime,
    previous_poll_attempt_count: int,
) -> GeminiInteractionSnapshot:
    """Poll Gemini for the current interaction status and normalize the result."""
    interaction = await asyncio.to_thread(_get_interaction_sync, settings=settings, interaction_id=interaction_id)
    return _build_interaction_snapshot(
        interaction=interaction,
        poll_interval_seconds=settings.research_poll_interval_seconds,
        submitted_at=submitted_at,
        poll_attempt_count=previous_poll_attempt_count + 1,
    )


def _generate_concise_result_sync(
    *,
    settings: SignalsSettings,
    system_prompt: str,
    summary_request: ResearchSummaryRequest,
) -> Any:
    """Synchronously call the standard Gemini model for concise-result generation."""
    genai, types = _import_genai_modules()
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
    return client.models.generate_content(
        model=settings.gemini_summary_model,
        contents=build_summary_contents(request=summary_request),
        config=types.GenerateContentConfig(system_instruction=system_prompt),
    )


def _extract_generated_text(*, response: Any) -> str:
    """Extract text from a Gemini generate-content response."""
    response_text = _read_provider_value(source=response, field_names=("text",))
    if isinstance(response_text, str) and response_text.strip():
        return response_text.strip()
    candidates = _normalize_provider_list(value=_read_provider_value(source=response, field_names=("candidates",)))
    for candidate in candidates:
        content = _read_provider_value(source=candidate, field_names=("content",))
        parts = _normalize_provider_list(value=_read_provider_value(source=content, field_names=("parts",)))
        for part in parts:
            part_text = _read_provider_value(source=part, field_names=("text",))
            if isinstance(part_text, str) and part_text.strip():
                return part_text.strip()
    raise ValueError("Gemini summarization returned no text output.")


async def generate_concise_result(
    *,
    settings: SignalsSettings,
    system_prompt: str,
    summary_request: ResearchSummaryRequest,
) -> tuple[str, str]:
    """Generate the concise result from the raw research document using Gemini standard inference."""
    response = await asyncio.to_thread(
        _generate_concise_result_sync,
        settings=settings,
        system_prompt=system_prompt,
        summary_request=summary_request,
    )
    return _extract_generated_text(response=response), settings.gemini_summary_model
