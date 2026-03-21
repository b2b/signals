from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

from models import PromptReference, Research, ResearchSummaryRequest, SignalsSettings


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def resolve_repository_path(*, repository_relative_path: str) -> Path:
    """Resolve a repository-relative path into an absolute filesystem path."""
    return REPOSITORY_ROOT / repository_relative_path


def load_prompt_reference(*, prompt_path: str) -> tuple[PromptReference, str]:
    """Load the summarization prompt text and build reproducibility metadata for it."""
    absolute_prompt_path = resolve_repository_path(repository_relative_path=prompt_path)
    prompt_text = absolute_prompt_path.read_text(encoding="utf-8").strip()
    return (
        PromptReference(
            prompt_path=prompt_path,
            prompt_label=absolute_prompt_path.stem,
            prompt_sha256=sha256(prompt_text.encode("utf-8")).hexdigest(),
        ),
        prompt_text,
    )


def build_deep_research_input(*, topic: str) -> str:
    """Build the Deep Research prompt used for the long-running Gemini agent call."""
    return (
        "Conduct a deep research investigation into the topic below, focusing on the most recent "
        "and most decision-relevant developments.\n\n"
        f"Topic: {topic}\n\n"
        "Requirements:\n"
        "- Prioritize recent developments, and include concrete dates when they materially affect interpretation.\n"
        "- Surface regulatory, financial, competitive, geopolitical, and technology shifts when relevant.\n"
        "- Distinguish confirmed facts from forecasts or company guidance.\n"
        "- Include source-backed findings throughout the report.\n"
        "- Produce a detailed final report that is suitable for later executive summarization."
    )


def build_summary_contents(*, request: ResearchSummaryRequest) -> str:
    """Build the standard model input for concise-result generation."""
    return (
        f"Research topic:\n{request.topic}\n\n"
        "Research report:\n"
        f"{request.raw_research_text}"
    )


def build_result_url(*, settings: SignalsSettings, research: Research) -> str:
    """Build the deep link sent in the completion email for opening the result page."""
    base_url = settings.public_base_url.rstrip("/")
    escaped_token = quote(research.result_access_token, safe="")
    return f"{base_url}/results/{research.research_id}?token={escaped_token}"


def build_result_audio_url(*, settings: SignalsSettings, research: Research) -> str:
    """Build the tokenized audio URL used by the SPA audio player."""
    base_url = settings.public_base_url.rstrip("/")
    escaped_token = quote(research.result_access_token, safe="")
    return f"{base_url}/results/{research.research_id}/audio?token={escaped_token}"
