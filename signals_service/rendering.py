from __future__ import annotations

from datetime import UTC, datetime
from html import escape
import re
from jinja2 import Environment

from models import ResearchCompletionEmailContext


def _render_inline_markdown(*, value: str) -> str:
    """Render the limited inline markdown used by Signals summaries."""
    rendered_value = escape(value)
    rendered_value = re.sub(
        r"\[(.+?)\]\((https?://[^)]+)\)",
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>',
        rendered_value,
    )
    rendered_value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", rendered_value)
    rendered_value = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered_value)
    return rendered_value


def render_markdown_to_html(*, markdown_text: str) -> str:
    """Render markdown into HTML suitable for the result page and email-adjacent views."""
    html_parts: list[str] = []
    open_list_tag: str | None = None

    def close_list() -> None:
        nonlocal open_list_tag
        if open_list_tag is not None:
            html_parts.append(f"</{open_list_tag}>")
            open_list_tag = None

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            close_list()
            continue

        ordered_list_match = re.match(r"^\d+\.\s+(.*)$", line)
        unordered_list_match = re.match(r"^(?:-|\*)\s+(.*)$", line)

        if ordered_list_match:
            if open_list_tag != "ol":
                close_list()
                open_list_tag = "ol"
                html_parts.append("<ol>")
            html_parts.append(f"<li>{_render_inline_markdown(value=ordered_list_match.group(1))}</li>")
            continue

        if unordered_list_match:
            if open_list_tag != "ul":
                close_list()
                open_list_tag = "ul"
                html_parts.append("<ul>")
            html_parts.append(f"<li>{_render_inline_markdown(value=unordered_list_match.group(1))}</li>")
            continue

        close_list()
        if line.startswith("### "):
            html_parts.append(f"<h3>{_render_inline_markdown(value=line[4:])}</h3>")
            continue
        if line.startswith("## "):
            html_parts.append(f"<h2>{_render_inline_markdown(value=line[3:])}</h2>")
            continue
        if line.startswith("# "):
            html_parts.append(f"<h1>{_render_inline_markdown(value=line[2:])}</h1>")
            continue
        html_parts.append(f"<p>{_render_inline_markdown(value=line)}</p>")

    close_list()
    return "\n".join(html_parts)


def format_utc_timestamp(*, value: datetime) -> str:
    """Render a UTC timestamp into a concise human-friendly label."""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def render_completion_email_bodies(
    *,
    template_environment: Environment,
    context: ResearchCompletionEmailContext,
) -> tuple[str, str]:
    """Render the completion email bodies for Agentmail."""
    html_body = template_environment.get_template("emails/research_complete.html").render(
        topic=context.topic,
        recipient_email=context.recipient_email,
        result_url=context.result_url,
        completed_at_label=format_utc_timestamp(value=context.completed_at),
    )
    text_body = (
        "Your Signals research is ready.\n\n"
        f"Topic: {context.topic}\n"
        f"Completed: {format_utc_timestamp(value=context.completed_at)}\n\n"
        "Open the result in your browser:\n"
        f"{context.result_url}\n"
    )
    return text_body, html_body
