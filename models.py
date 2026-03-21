from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}$", re.IGNORECASE)
DOMAIN_PATTERN = re.compile(r"^(?:[A-Z0-9](?:[A-Z0-9\-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}$", re.IGNORECASE)
MAX_TOPIC_LENGTH = 2_000


def _require_string(*, value: object, field_name: str) -> str:
    """Validate that a raw input value is a string before any normalization."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string.")
    return value


def normalize_non_empty_text(value: object) -> str:
    """Trim text while preserving internal formatting and rejecting blank inputs."""
    normalized_value = _require_string(value=value, field_name="text").strip()
    if not normalized_value:
        raise ValueError("Text value must not be empty.")
    return normalized_value


def normalize_topic(value: object) -> str:
    """Normalize a research topic without altering its substantive content."""
    normalized_value = normalize_non_empty_text(value)
    if len(normalized_value) > MAX_TOPIC_LENGTH:
        raise ValueError(f"Topic must be at most {MAX_TOPIC_LENGTH} characters long.")
    return normalized_value


def normalize_email(value: object) -> str:
    """Normalize an email address for case-insensitive whitelist matching."""
    normalized_value = _require_string(value=value, field_name="email").strip().lower()
    if not normalized_value:
        raise ValueError("Email address must not be empty.")
    if not EMAIL_PATTERN.fullmatch(normalized_value):
        raise ValueError("Email address must be in a valid format.")
    return normalized_value


def normalize_domain(value: object) -> str:
    """Normalize a domain value for consistent MongoDB lookups."""
    normalized_value = _require_string(value=value, field_name="domain").strip().lower()
    normalized_value = normalized_value.removeprefix("@").rstrip(".")
    if not normalized_value:
        raise ValueError("Domain must not be empty.")
    if not DOMAIN_PATTERN.fullmatch(normalized_value):
        raise ValueError("Domain must be in a valid format.")
    return normalized_value


def extract_domain_from_email(*, email: str) -> str:
    """Extract a normalized domain from a normalized email address."""
    return email.split("@", maxsplit=1)[1]


NormalizedEmail = Annotated[str, BeforeValidator(normalize_email)]
NormalizedDomain = Annotated[str, BeforeValidator(normalize_domain)]
NonEmptyText = Annotated[str, BeforeValidator(normalize_non_empty_text)]
ResearchTopic = Annotated[str, BeforeValidator(normalize_topic)]


class SignalsBaseModel(BaseModel):
    """Shared Pydantic base for all Signals models."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class SubmissionSource(StrEnum):
    """Supported request entry points for new research jobs."""

    SPA = "spa"


class WhitelistEntryStatus(StrEnum):
    """Lifecycle states for whitelist records stored in MongoDB."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class WhitelistMatchType(StrEnum):
    """Ways an incoming requester can be authorized."""

    EXACT_EMAIL = "exact_email"
    DOMAIN = "domain"


class WhitelistCollectionName(StrEnum):
    """MongoDB collections used during whitelist validation."""

    WHITELISTED_EMAILS = "whitelisted_emails"
    WHITELISTED_EMAIL_DOMAINS = "whitelisted_email_domains"


class WhitelistDecisionReason(StrEnum):
    """Outcomes produced by the whitelist validation step."""

    EXACT_EMAIL_MATCH = "exact_email_match"
    DOMAIN_MATCH = "domain_match"
    NOT_WHITELISTED = "not_whitelisted"


class GeminiAgentName(StrEnum):
    """Gemini agent identifiers currently approved for the Signals workflow."""

    DEEP_RESEARCH = "deep-research-pro-preview-12-2025"


class GeminiInteractionStatus(StrEnum):
    """Interaction states documented by the Gemini Interactions API."""

    IN_PROGRESS = "in_progress"
    REQUIRES_ACTION = "requires_action"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INCOMPLETE = "incomplete"


class GeminiUsageModality(StrEnum):
    """Gemini usage modalities reported in interaction token accounting."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class ResearchStatus(StrEnum):
    """End-to-end workflow stages for a persisted research request."""

    RESEARCH_QUEUED = "research_queued"
    RESEARCH_IN_PROGRESS = "research_in_progress"
    RESEARCH_COMPLETED = "research_completed"
    SUMMARIZATION_IN_PROGRESS = "summarization_in_progress"
    READY_TO_EMAIL = "ready_to_email"
    EMAIL_SENDING = "email_sending"
    COMPLETED = "completed"
    RESEARCH_FAILED = "research_failed"
    SUMMARIZATION_FAILED = "summarization_failed"
    EMAIL_FAILED = "email_failed"
    CANCELLED = "cancelled"


class SummaryStatus(StrEnum):
    """States for the concise-result generation stage."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class NotificationChannel(StrEnum):
    """Supported delivery channels for research completion notifications."""

    EMAIL = "email"


class NotificationStatus(StrEnum):
    """States for outbound research completion notifications."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class ResearchFailureStage(StrEnum):
    """Pipeline stages that can fail during processing."""

    RESEARCH = "research"
    SUMMARIZATION = "summarization"
    EMAIL = "email"


class ResearchLifecycleEventType(StrEnum):
    """Audit event types stored on a research document."""

    REQUEST_ACCEPTED = "request_accepted"
    WHITELIST_VALIDATED = "whitelist_validated"
    GEMINI_SUBMITTED = "gemini_submitted"
    GEMINI_POLLED = "gemini_polled"
    RESEARCH_COMPLETED = "research_completed"
    RESEARCH_FAILED = "research_failed"
    SUMMARIZATION_STARTED = "summarization_started"
    SUMMARIZATION_COMPLETED = "summarization_completed"
    SUMMARIZATION_FAILED = "summarization_failed"
    EMAIL_STARTED = "email_started"
    EMAIL_SENT = "email_sent"
    EMAIL_FAILED = "email_failed"
    STATUS_CHANGED = "status_changed"


class SignalsSettings(BaseSettings):
    """Application configuration hydrated from environment variables elsewhere in the service."""

    model_config = SettingsConfigDict(
        extra="forbid",
        validate_assignment=True,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    gemini_api_key: SecretStr = Field(..., description="Gemini API key used for the Interactions and Deep Research APIs.")
    mongodb_url: SecretStr = Field(..., description="MongoDB Atlas connection string for the active deployment.")
    database_name: NonEmptyText = Field(..., description="MongoDB database name used by the Signals service.")
    agentmail_api_key: SecretStr = Field(..., description="Agentmail API key used for outbound completion emails.")
    email_address: NormalizedEmail = Field(..., description="Internal sender mailbox address used as the outbound Agentmail inbox.")
    gemini_deep_research_agent: GeminiAgentName = Field(
        default=GeminiAgentName.DEEP_RESEARCH,
        description="Gemini Deep Research agent identifier configured for long-running research tasks.",
    )
    gemini_summary_model: NonEmptyText = Field(
        default="gemini-3-pro-preview",
        description="Gemini model identifier used for concise-result generation after research completes.",
    )
    research_poll_interval_seconds: int = Field(
        default=60,
        description="Polling cadence, in seconds, for checking background Gemini interaction status.",
        ge=1,
    )
    summarization_prompt_path: NonEmptyText = Field(
        default="prompts/system_prompt_1.md",
        description="Repository-relative path to the system prompt used for concise-result generation.",
    )
    public_base_url: NonEmptyText = Field(
        default="http://localhost:8000",
        description="Public base URL used to construct deep links back to the Signals SPA and result pages.",
    )


class ResearchRequesterSnapshot(SignalsBaseModel):
    """Denormalized requester information embedded directly into each research document."""

    email: NormalizedEmail = Field(..., description="Normalized requester email address that submitted the research topic.")
    email_domain: NormalizedDomain = Field(..., description="Normalized domain extracted from the requester email for whitelist checks.")

    @model_validator(mode="before")
    @classmethod
    def populate_email_domain(cls, data: object) -> object:
        """Fill the email-domain snapshot automatically from the normalized email value."""
        if isinstance(data, dict):
            email_value = data.get("email")
            if email_value is not None:
                normalized_email = normalize_email(email_value)
                return {
                    **data,
                    "email": normalized_email,
                    "email_domain": data.get("email_domain", extract_domain_from_email(email=normalized_email)),
                }
        return data


class WhitelistedEmail(SignalsBaseModel):
    """Authoritative whitelist document for a single explicitly approved email address."""

    whitelisted_email_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Business UUID for this whitelisted email record.",
    )
    email: NormalizedEmail = Field(..., description="Normalized individual email address that is allowed to use the service.")
    email_domain: NormalizedDomain = Field(..., description="Normalized domain derived from the whitelisted email address.")
    status: WhitelistEntryStatus = Field(
        default=WhitelistEntryStatus.ACTIVE,
        description="Whether this whitelist record is currently active for authorization checks.",
    )
    display_name: str | None = Field(
        default=None,
        description="Optional human-friendly label for the person who owns this whitelisted email address.",
    )
    notes: str | None = Field(
        default=None,
        description="Optional internal notes explaining why this email address is whitelisted.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when this whitelist record was created (UTC).",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when this whitelist record was last updated (UTC).",
    )
    version: int = Field(default=1, description="Record version, starts at 1 and increments on each update.", ge=1)

    @model_validator(mode="before")
    @classmethod
    def populate_email_domain(cls, data: object) -> object:
        """Ensure the derived domain is stored alongside the explicit email value."""
        if isinstance(data, dict):
            email_value = data.get("email")
            if email_value is not None:
                normalized_email = normalize_email(email_value)
                return {
                    **data,
                    "email": normalized_email,
                    "email_domain": data.get("email_domain", extract_domain_from_email(email=normalized_email)),
                }
        return data


class WhitelistedEmailDomain(SignalsBaseModel):
    """Authoritative whitelist document for an approved corporate email domain."""

    whitelisted_email_domain_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Business UUID for this whitelisted email-domain record.",
    )
    domain: NormalizedDomain = Field(..., description="Normalized corporate email domain that is allowed to access the service.")
    status: WhitelistEntryStatus = Field(
        default=WhitelistEntryStatus.ACTIVE,
        description="Whether this domain whitelist record is currently active for authorization checks.",
    )
    organization_name: str | None = Field(
        default=None,
        description="Optional display name for the organization that owns the whitelisted domain.",
    )
    notes: str | None = Field(
        default=None,
        description="Optional internal notes explaining why this email domain is whitelisted.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when this domain whitelist record was created (UTC).",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when this domain whitelist record was last updated (UTC).",
    )
    version: int = Field(default=1, description="Record version, starts at 1 and increments on each update.", ge=1)


class WhitelistDecision(SignalsBaseModel):
    """Normalized outcome of validating a requester against the whitelist collections."""

    whitelist_decision_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Business UUID for this whitelist validation decision.",
    )
    allowed: bool = Field(..., description="Whether the requester is authorized to proceed with research generation.")
    reason: WhitelistDecisionReason = Field(..., description="Structured explanation for the whitelist authorization outcome.")
    match_type: WhitelistMatchType | None = Field(
        default=None,
        description="How the requester matched the whitelist when access was granted.",
    )
    matched_collection_name: WhitelistCollectionName | None = Field(
        default=None,
        description="Name of the MongoDB whitelist collection that produced the authorization match.",
    )
    matched_entry_id: str | None = Field(
        default=None,
        description="Business UUID of the specific whitelist document that authorized the requester.",
    )
    matched_value: str | None = Field(
        default=None,
        description="Exact normalized whitelist value that matched the incoming requester.",
    )
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the whitelist validation was performed (UTC).",
    )

    @model_validator(mode="after")
    def validate_consistency(self) -> WhitelistDecision:
        """Keep successful and rejected authorization payloads internally coherent."""
        if self.allowed and self.match_type is None:
            raise ValueError("Allowed whitelist decisions must include a match_type.")
        if self.allowed and self.matched_collection_name is None:
            raise ValueError("Allowed whitelist decisions must include matched_collection_name.")
        if self.allowed and self.matched_entry_id is None:
            raise ValueError("Allowed whitelist decisions must include matched_entry_id.")
        if self.allowed and self.matched_value is None:
            raise ValueError("Allowed whitelist decisions must include matched_value.")
        if not self.allowed and any(
            value is not None for value in (self.match_type, self.matched_collection_name, self.matched_entry_id, self.matched_value)
        ):
            raise ValueError("Rejected whitelist decisions must not include matched whitelist metadata.")
        return self


class ResearchSubmissionRequest(SignalsBaseModel):
    """FastAPI request payload accepted from the SPA when a user submits a research topic."""

    topic: ResearchTopic = Field(..., description="User-supplied research topic focused on recent developments.")
    email: NormalizedEmail = Field(..., description="Requester email address used for whitelist validation and delivery.")


class ResearchSubmissionAcceptedResponse(SignalsBaseModel):
    """API response returned when a research request passes whitelist validation and is queued."""

    accepted: Literal[True] = Field(default=True, description="Whether the research submission was accepted for processing.")
    research_id: str = Field(..., description="Business UUID of the persisted research workflow document.")
    status: ResearchStatus = Field(..., description="Initial persisted workflow status for the accepted research request.")
    message: NonEmptyText = Field(..., description="User-facing confirmation message displayed by the SPA.")
    email: NormalizedEmail = Field(..., description="Normalized requester email address that will receive the completion email.")
    estimated_poll_interval_seconds: int = Field(
        default=60,
        description="Expected background polling cadence, in seconds, for checking research completion.",
        ge=1,
    )


class ResearchSubmissionRejectedResponse(SignalsBaseModel):
    """API response returned when a requester fails whitelist validation."""

    accepted: Literal[False] = Field(default=False, description="Whether the research submission was accepted for processing.")
    reason: Literal[WhitelistDecisionReason.NOT_WHITELISTED] = Field(
        default=WhitelistDecisionReason.NOT_WHITELISTED,
        description="Structured reason explaining why the research request was rejected.",
    )
    message: NonEmptyText = Field(..., description="User-facing rejection message displayed by the SPA.")
    email: NormalizedEmail = Field(..., description="Normalized requester email address that was denied access.")


class ResearchStatusResponse(SignalsBaseModel):
    """API response used by the SPA to poll the current processing status of a research workflow."""

    research_id: str = Field(..., description="Business UUID of the research workflow document.")
    topic: ResearchTopic = Field(..., description="Original topic submitted for research.")
    email: NormalizedEmail = Field(..., description="Normalized requester email address associated with the workflow.")
    status: ResearchStatus = Field(..., description="Current end-to-end workflow status for the research request.")
    gemini_status: GeminiInteractionStatus | None = Field(
        default=None,
        description="Latest Gemini interaction status for the research request, when a Gemini interaction exists.",
    )
    summary_status: SummaryStatus = Field(..., description="Current status of concise-result generation.")
    notification_status: NotificationStatus | None = Field(
        default=None,
        description="Latest outbound notification status for the workflow, when an email notification exists.",
    )
    concise_result_available: bool = Field(
        ...,
        description="Whether a concise result has been generated and is available for rendering on the result page.",
    )
    created_at: datetime = Field(..., description="Timestamp when the research workflow document was created (UTC).")
    updated_at: datetime = Field(..., description="Timestamp when the research workflow document was last updated (UTC).")
    completed_at: datetime | None = Field(
        default=None,
        description="Timestamp when the full workflow completed successfully, including notification delivery (UTC).",
    )
    failed_at: datetime | None = Field(
        default=None,
        description="Timestamp when the workflow most recently entered a terminal failure state (UTC).",
    )
    latest_failure_message: str | None = Field(
        default=None,
        description="Most recent failure message captured for the workflow, when one exists.",
    )


class GeminiUrlCitation(SignalsBaseModel):
    """Citation metadata for a web source referenced inside Gemini-generated text."""

    type: Literal["url_citation"] = Field(default="url_citation", description="Discriminator for a Gemini URL citation annotation.")
    url: str | None = Field(default=None, description="Canonical URL cited by the generated text segment.")
    title: str | None = Field(default=None, description="Optional page title associated with the cited URL.")
    start_index: int | None = Field(
        default=None,
        description="Start offset of the attributed text segment within the generated output.",
        ge=0,
    )
    end_index: int | None = Field(
        default=None,
        description="Exclusive end offset of the attributed text segment within the generated output.",
        ge=0,
    )


class GeminiFileCitation(SignalsBaseModel):
    """Citation metadata for a private document or uploaded file referenced by Gemini."""

    type: Literal["file_citation"] = Field(default="file_citation", description="Discriminator for a Gemini file citation annotation.")
    document_uri: str | None = Field(default=None, description="URI of the cited file, when Gemini exposes it.")
    file_name: str | None = Field(default=None, description="Filename of the cited file, when available from Gemini.")
    source: str | None = Field(default=None, description="Human-readable file source text emitted by Gemini, when present.")
    start_index: int | None = Field(
        default=None,
        description="Start offset of the attributed text segment within the generated output.",
        ge=0,
    )
    end_index: int | None = Field(
        default=None,
        description="Exclusive end offset of the attributed text segment within the generated output.",
        ge=0,
    )


class GeminiPlaceReviewSnippet(SignalsBaseModel):
    """Review metadata embedded inside a Google Maps place citation."""

    title: str | None = Field(default=None, description="Title of the review snippet attached to the place citation.")
    url: str | None = Field(default=None, description="URL pointing to the underlying review snippet, when available.")
    review_id: str | None = Field(default=None, description="Provider review identifier for the cited review snippet.")


class GeminiPlaceCitation(SignalsBaseModel):
    """Citation metadata for a place sourced from Google Maps tooling."""

    type: Literal["place_citation"] = Field(default="place_citation", description="Discriminator for a Gemini place citation annotation.")
    place_id: str | None = Field(default=None, description="Place resource identifier in places/{place_id} format, when available.")
    name: str | None = Field(default=None, description="Human-friendly name of the cited place.")
    url: str | None = Field(default=None, description="Canonical URL for the cited place, when available.")
    review_snippets: GeminiPlaceReviewSnippet | None = Field(
        default=None,
        description="Optional review snippet metadata included by Gemini for the cited place.",
    )
    start_index: int | None = Field(
        default=None,
        description="Start offset of the attributed text segment within the generated output.",
        ge=0,
    )
    end_index: int | None = Field(
        default=None,
        description="Exclusive end offset of the attributed text segment within the generated output.",
        ge=0,
    )


GeminiTextAnnotation = Annotated[
    GeminiUrlCitation | GeminiFileCitation | GeminiPlaceCitation,
    Field(discriminator="type"),
]


class GeminiTextOutput(SignalsBaseModel):
    """Text output block extracted from a Gemini interaction result."""

    type: Literal["text"] = Field(default="text", description="Discriminator for a Gemini text output block.")
    text: NonEmptyText = Field(..., description="Full text emitted by Gemini for this output block.")
    annotations: list[GeminiTextAnnotation] = Field(
        default_factory=list,
        description="Source annotations attached to the generated text block, when Gemini returns them.",
    )


class GeminiUsageByModality(SignalsBaseModel):
    """Token counts grouped by modality inside Gemini usage accounting."""

    modality: GeminiUsageModality = Field(..., description="Modality associated with this Gemini token count.")
    tokens: int = Field(..., description="Number of tokens attributed to the given modality.", ge=0)


class GeminiInteractionUsage(SignalsBaseModel):
    """Token accounting returned by Gemini for a completed or in-progress interaction."""

    input_tokens_by_modality: list[GeminiUsageByModality] = Field(
        default_factory=list,
        description="Breakdown of input token counts grouped by modality.",
    )
    tool_use_tokens_by_modality: list[GeminiUsageByModality] = Field(
        default_factory=list,
        description="Breakdown of tool-use token counts grouped by modality, when available.",
    )
    total_cached_tokens: int | None = Field(default=None, description="Total cached input tokens reported by Gemini.", ge=0)
    total_input_tokens: int | None = Field(default=None, description="Total input tokens reported by Gemini.", ge=0)
    total_output_tokens: int | None = Field(default=None, description="Total output tokens reported by Gemini.", ge=0)
    total_reasoning_tokens: int | None = Field(default=None, description="Total reasoning tokens reported by Gemini.", ge=0)
    total_tool_use_tokens: int | None = Field(default=None, description="Total tool-use tokens reported by Gemini.", ge=0)
    total_tokens: int | None = Field(default=None, description="Total token count for the full Gemini interaction.", ge=0)


class GeminiInteractionError(SignalsBaseModel):
    """Error payload returned by Gemini when an interaction fails."""

    code: str | None = Field(default=None, description="Provider error code or URI identifying the failure type.")
    message: str | None = Field(default=None, description="Human-readable error message returned by Gemini.")


class GeminiDeepResearchRequest(SignalsBaseModel):
    """Validated Gemini Interactions API payload used to start a background Deep Research task."""

    input: NonEmptyText = Field(..., description="Prompt text sent to the Gemini Deep Research agent.")
    agent: GeminiAgentName = Field(
        default=GeminiAgentName.DEEP_RESEARCH,
        description="Gemini agent identifier used for the research job.",
    )
    background: Literal[True] = Field(
        default=True,
        description="Gemini background execution flag, required for the Deep Research agent.",
    )


class GeminiInteractionSnapshot(SignalsBaseModel):
    """Stable internal snapshot of the Gemini interaction data that Signals needs to persist."""

    interaction_id: str = Field(..., description="Gemini interaction identifier returned when the background task is created.")
    agent_name: GeminiAgentName | None = Field(
        default=None,
        description="Gemini agent identifier used for the interaction, when this was an agent-based request.",
    )
    model_name: str | None = Field(
        default=None,
        description="Gemini model identifier used for the interaction, when a direct model was called instead of an agent.",
    )
    object_type: Literal["interaction"] = Field(
        default="interaction",
        description="Gemini object type persisted for this interaction snapshot.",
    )
    status: GeminiInteractionStatus = Field(..., description="Latest known Gemini interaction status.")
    previous_interaction_id: str | None = Field(
        default=None,
        description="Previous Gemini interaction identifier when this interaction continues prior context.",
    )
    final_text_output: GeminiTextOutput | None = Field(
        default=None,
        description="Final Gemini text output block extracted from the interaction, when one is available.",
    )
    usage: GeminiInteractionUsage | None = Field(
        default=None,
        description="Token-usage metadata returned by Gemini for the interaction.",
    )
    error: GeminiInteractionError | None = Field(
        default=None,
        description="Gemini error payload captured when the interaction fails.",
    )
    created_at: datetime | None = Field(
        default=None,
        description="Timestamp when Gemini created the interaction resource (UTC).",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="Timestamp when Gemini last updated the interaction resource (UTC).",
    )
    submitted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when Signals submitted the background Gemini interaction (UTC).",
    )
    last_polled_at: datetime | None = Field(
        default=None,
        description="Timestamp of the latest poll performed against the Gemini interaction (UTC).",
    )
    next_poll_at: datetime | None = Field(
        default=None,
        description="Timestamp when the poller should next check this Gemini interaction (UTC).",
    )
    poll_interval_seconds: int = Field(
        default=60,
        description="Polling cadence, in seconds, used for this Gemini interaction.",
        ge=1,
    )
    poll_attempt_count: int = Field(
        default=0,
        description="Number of status-poll attempts made against the Gemini interaction.",
        ge=0,
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Timestamp when Gemini first reported the interaction as completed (UTC).",
    )
    failed_at: datetime | None = Field(
        default=None,
        description="Timestamp when Gemini first reported the interaction as failed (UTC).",
    )
    cancelled_at: datetime | None = Field(
        default=None,
        description="Timestamp when Gemini first reported the interaction as cancelled (UTC).",
    )


class ResearchFailure(SignalsBaseModel):
    """Normalized failure record captured for research, summarization, or email stages."""

    research_failure_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Business UUID for this failure record.",
    )
    stage: ResearchFailureStage = Field(..., description="Pipeline stage where the failure occurred.")
    message: NonEmptyText = Field(..., description="Human-readable failure message stored for debugging and audit.")
    provider: str | None = Field(
        default=None,
        description="External provider responsible for the failure, when the failure originated from an integration.",
    )
    provider_code: str | None = Field(
        default=None,
        description="External provider error code or status identifier, when available.",
    )
    retryable: bool = Field(..., description="Whether the failure is considered safe to retry automatically.")
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the failure was recorded (UTC).",
    )


class PromptReference(SignalsBaseModel):
    """Reference metadata for a prompt asset stored in the repository."""

    prompt_path: NonEmptyText = Field(..., description="Repository-relative path to the prompt file used for an LLM call.")
    prompt_label: NonEmptyText = Field(..., description="Short human-friendly label for the referenced prompt.")
    prompt_version: int = Field(default=1, description="Prompt version number, starts at 1 and increments on changes.", ge=1)
    prompt_sha256: str | None = Field(
        default=None,
        description="Optional SHA-256 hash of the prompt file contents for reproducibility.",
    )


class ResearchSummaryRequest(SignalsBaseModel):
    """Validated input payload for the summarization step that produces the concise result."""

    research_id: str = Field(..., description="Business UUID of the research workflow being summarized.")
    topic: ResearchTopic = Field(..., description="Original research topic used to contextualize summarization.")
    raw_research_text: NonEmptyText = Field(..., description="Full raw research report text produced by Gemini.")
    prompt_reference: PromptReference = Field(..., description="Prompt asset metadata used during summarization.")


class ResearchSummary(SignalsBaseModel):
    """Metadata for the concise-result generation stage stored inside the research document."""

    research_summary_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Business UUID for this summarization stage record.",
    )
    status: SummaryStatus = Field(default=SummaryStatus.PENDING, description="Current status of concise-result generation.")
    provider: str = Field(default="gemini", description="LLM provider used for the summarization step.")
    model_name: str | None = Field(
        default=None,
        description="Model identifier used for summarization, once the inference implementation is chosen.",
    )
    prompt_reference: PromptReference = Field(..., description="Prompt asset metadata used for concise-result generation.")
    input_character_count: int = Field(
        default=0,
        description="Character count of the raw research text supplied to the summarization stage.",
        ge=0,
    )
    output_character_count: int = Field(
        default=0,
        description="Character count of the generated concise result after summarization completes.",
        ge=0,
    )
    started_at: datetime | None = Field(
        default=None,
        description="Timestamp when summarization started (UTC).",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Timestamp when summarization completed successfully (UTC).",
    )
    failed_at: datetime | None = Field(
        default=None,
        description="Timestamp when summarization failed (UTC).",
    )
    failure: ResearchFailure | None = Field(
        default=None,
        description="Failure metadata captured when concise-result generation does not succeed.",
    )
    version: int = Field(default=1, description="Stage version, starts at 1 and increments on each update.", ge=1)


class ResearchCompletionEmailContext(SignalsBaseModel):
    """Template context used to render the research completion email sent to the requester."""

    research_id: str = Field(..., description="Business UUID of the completed research workflow.")
    topic: ResearchTopic = Field(..., description="Original topic included in the completion email context.")
    recipient_email: NormalizedEmail = Field(..., description="Normalized recipient email address for the completion email.")
    result_url: NonEmptyText = Field(..., description="Deep link back to the Signals result page where the concise result can be viewed.")
    completed_at: datetime = Field(..., description="Timestamp when the research workflow reached completion (UTC).")


class AgentmailSendMessageRequest(SignalsBaseModel):
    """Validated outbound message payload for the Agentmail send-message operation."""

    inbox_id: NormalizedEmail = Field(..., description="Sender inbox identifier used for the outbound Agentmail message.")
    to: list[NormalizedEmail] = Field(..., description="Primary recipients for the outbound email message.")
    subject: NonEmptyText = Field(..., description="Email subject line presented to the recipient.")
    text: NonEmptyText = Field(..., description="Plain-text email body used as the universal fallback body.")
    html: NonEmptyText = Field(..., description="HTML email body rendered for rich email clients.")
    cc: list[NormalizedEmail] = Field(default_factory=list, description="Carbon-copy recipients for the outbound email.")
    bcc: list[NormalizedEmail] = Field(default_factory=list, description="Blind-carbon-copy recipients for the outbound email.")
    reply_to: NormalizedEmail | None = Field(
        default=None,
        description="Optional reply-to email address used instead of the sender inbox.",
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Agentmail labels attached to the outbound message for downstream filtering or analytics.",
    )

    @field_validator("to", "cc", "bcc", mode="before")
    @classmethod
    def coerce_recipients_to_list(cls, value: object) -> object:
        """Allow a single recipient string while persisting a normalized list shape."""
        if isinstance(value, str):
            return [value]
        return value

    @model_validator(mode="after")
    def validate_recipient_limits(self) -> AgentmailSendMessageRequest:
        """Enforce the documented Agentmail recipient cap across to, cc, and bcc."""
        recipient_count = len(self.to) + len(self.cc) + len(self.bcc)
        if recipient_count == 0:
            raise ValueError("At least one email recipient must be provided.")
        if recipient_count > 50:
            raise ValueError("Agentmail supports at most 50 combined to, cc, and bcc recipients per send.")
        return self


class AgentmailSendMessageResponse(SignalsBaseModel):
    """Minimal response metadata retained from a successful Agentmail send operation."""

    message_id: str = Field(..., description="Agentmail message identifier for the outbound email.")
    thread_id: str | None = Field(
        default=None,
        description="Agentmail thread identifier created or reused for the outbound email.",
    )


class ResearchEmailNotification(SignalsBaseModel):
    """Persisted outbound email record embedded inside the research document."""

    research_email_notification_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Business UUID for this email notification record.",
    )
    channel: NotificationChannel = Field(
        default=NotificationChannel.EMAIL,
        description="Delivery channel used for notifying the requester that research is complete.",
    )
    status: NotificationStatus = Field(default=NotificationStatus.PENDING, description="Current state of the outbound notification.")
    provider: str = Field(default="agentmail", description="Outbound email provider used for this notification.")
    from_email: NormalizedEmail = Field(..., description="Sender email address used for the outbound research notification.")
    to_email: NormalizedEmail = Field(..., description="Recipient email address that should receive the research results.")
    subject: str | None = Field(default=None, description="Rendered email subject line used for the outbound notification.")
    text_body: str | None = Field(default=None, description="Rendered plain-text body used for the outbound notification.")
    html_body: str | None = Field(default=None, description="Rendered HTML body used for the outbound notification.")
    labels: list[str] = Field(
        default_factory=list,
        description="Agentmail labels attached to the outbound research notification.",
    )
    provider_message_id: str | None = Field(
        default=None,
        description="Agentmail message identifier returned after a successful send.",
    )
    provider_thread_id: str | None = Field(
        default=None,
        description="Agentmail thread identifier returned after a successful send.",
    )
    requested_at: datetime | None = Field(
        default=None,
        description="Timestamp when the service queued or attempted the outbound email send (UTC).",
    )
    sent_at: datetime | None = Field(
        default=None,
        description="Timestamp when the outbound email was sent successfully (UTC).",
    )
    failed_at: datetime | None = Field(
        default=None,
        description="Timestamp when the outbound email send failed (UTC).",
    )
    failure: ResearchFailure | None = Field(
        default=None,
        description="Failure metadata captured when the outbound email send does not succeed.",
    )
    version: int = Field(default=1, description="Notification version, starts at 1 and increments on each update.", ge=1)


class ResearchLifecycleEvent(SignalsBaseModel):
    """Append-only audit event stored on a research document for debugging and support."""

    research_lifecycle_event_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Business UUID for this lifecycle event.",
    )
    event_type: ResearchLifecycleEventType = Field(..., description="Structured type of workflow event that occurred.")
    message: NonEmptyText = Field(..., description="Human-readable explanation of what happened at this workflow event.")
    research_status: ResearchStatus = Field(..., description="Overall research status immediately after this event.")
    gemini_status: GeminiInteractionStatus | None = Field(
        default=None,
        description="Gemini interaction status immediately after this event, when relevant.",
    )
    summary_status: SummaryStatus | None = Field(
        default=None,
        description="Summarization status immediately after this event, when relevant.",
    )
    notification_status: NotificationStatus | None = Field(
        default=None,
        description="Notification status immediately after this event, when relevant.",
    )
    reference_id: str | None = Field(
        default=None,
        description="Related provider or business identifier associated with the event, when one exists.",
    )
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the workflow event occurred (UTC).",
    )


class Research(SignalsBaseModel):
    """Authoritative MongoDB document representing an accepted research workflow from submission to email delivery."""

    research_id: str = Field(default_factory=lambda: str(uuid4()), description="Business UUID for the research workflow document.")
    result_access_token: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Opaque token embedded in emailed deep links so only the recipient can open the rendered result page.",
    )
    topic: ResearchTopic = Field(..., description="Original research topic requested by the user.")
    requester: ResearchRequesterSnapshot = Field(..., description="Denormalized requester details embedded for single-query reads.")
    submission_source: SubmissionSource = Field(
        default=SubmissionSource.SPA,
        description="Entry point that submitted the research request.",
    )
    whitelist_decision: WhitelistDecision = Field(
        ...,
        description="Whitelist authorization result that allowed this research workflow to be created.",
    )
    status: ResearchStatus = Field(
        default=ResearchStatus.RESEARCH_QUEUED,
        description="Current end-to-end workflow status for the research request.",
    )
    gemini_request: GeminiDeepResearchRequest = Field(
        ...,
        description="Validated Gemini request payload used to create the background Deep Research interaction.",
    )
    gemini_interaction: GeminiInteractionSnapshot | None = Field(
        default=None,
        description="Latest stable snapshot of the Gemini interaction state for this research request.",
    )
    raw_research_text: NonEmptyText | None = Field(
        default=None,
        description="Full raw research report text returned by Gemini after the background job completes.",
    )
    raw_research_saved_at: datetime | None = Field(
        default=None,
        description="Timestamp when the raw research report text was first persisted to MongoDB (UTC).",
    )
    raw_research_citations: list[GeminiTextAnnotation] = Field(
        default_factory=list,
        description="Source annotations extracted from the final Gemini research text output.",
    )
    concise_result: NonEmptyText | None = Field(
        default=None,
        description="Concise summary text appended to the research document after summarization completes.",
    )
    summary: ResearchSummary = Field(
        default_factory=lambda: ResearchSummary(
            prompt_reference=PromptReference(
                prompt_path="prompts/system_prompt_1.md",
                prompt_label="system_prompt_1",
            )
        ),
        description="Summarization stage metadata for generating the concise result.",
    )
    email_notification: ResearchEmailNotification | None = Field(
        default=None,
        description="Outbound email metadata once the research completion notification is prepared or sent.",
    )
    failures: list[ResearchFailure] = Field(
        default_factory=list,
        description="Normalized failure records captured across the lifecycle of this research workflow.",
    )
    lifecycle_events: list[ResearchLifecycleEvent] = Field(
        default_factory=list,
        description="Append-only audit trail of significant workflow events for this research request.",
    )
    status_updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the overall research status was last updated (UTC).",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the research document was created (UTC).",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the research document was last updated (UTC).",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="Timestamp when the full workflow completed successfully, including outbound email delivery (UTC).",
    )
    failed_at: datetime | None = Field(
        default=None,
        description="Timestamp when the workflow most recently entered a terminal failure state (UTC).",
    )
    cancelled_at: datetime | None = Field(
        default=None,
        description="Timestamp when the workflow was cancelled or Gemini returned a cancelled terminal state (UTC).",
    )
    version: int = Field(default=1, description="Document version, starts at 1 and increments on each update.", ge=1)

    @model_validator(mode="after")
    def validate_authorization(self) -> Research:
        """Ensure only authorized requests are persisted and workflow state remains coherent."""
        if not self.whitelist_decision.allowed:
            raise ValueError("Research documents may only be created for allowed whitelist decisions.")
        if self.raw_research_text is None and self.raw_research_saved_at is not None:
            raise ValueError("raw_research_saved_at cannot be set when raw_research_text is missing.")
        if self.raw_research_text is not None and self.raw_research_saved_at is None:
            raise ValueError("raw_research_saved_at must be set when raw_research_text is stored.")
        if self.concise_result is not None and self.summary.status is not SummaryStatus.COMPLETED:
            raise ValueError("concise_result may only be present when summary.status is completed.")
        if self.summary.status is SummaryStatus.COMPLETED and self.concise_result is None:
            raise ValueError("summary.status cannot be completed without concise_result.")
        if self.status is ResearchStatus.RESEARCH_COMPLETED and self.raw_research_text is None:
            raise ValueError("research status cannot be research_completed without raw_research_text.")
        if self.status in {ResearchStatus.READY_TO_EMAIL, ResearchStatus.EMAIL_SENDING, ResearchStatus.COMPLETED} and self.concise_result is None:
            raise ValueError("Research cannot progress past summarization without concise_result.")
        if self.email_notification and self.email_notification.status is NotificationStatus.SENT and self.concise_result is None:
            raise ValueError("A sent email notification requires concise_result to be present.")
        if self.status is ResearchStatus.COMPLETED:
            if self.email_notification is None or self.email_notification.status is not NotificationStatus.SENT:
                raise ValueError("Completed research requires a sent email notification.")
            if self.completed_at is None:
                raise ValueError("completed_at must be set when the workflow status is completed.")
        if self.status in {ResearchStatus.RESEARCH_FAILED, ResearchStatus.SUMMARIZATION_FAILED, ResearchStatus.EMAIL_FAILED} and self.failed_at is None:
            raise ValueError("failed_at must be set when the workflow is in a failed terminal state.")
        if self.status is ResearchStatus.CANCELLED and self.cancelled_at is None:
            raise ValueError("cancelled_at must be set when the workflow status is cancelled.")
        return self
