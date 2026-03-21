from __future__ import annotations

import asyncio
from typing import Any

from models import AgentmailSendMessageRequest, AgentmailSendMessageResponse, SignalsSettings


def _read_provider_value(*, source: Any, field_names: tuple[str, ...]) -> Any:
    """Read a field from either an SDK object or a plain mapping."""
    for field_name in field_names:
        if isinstance(source, dict) and field_name in source:
            return source[field_name]
        if hasattr(source, field_name):
            return getattr(source, field_name)
    return None


def _import_agentmail():
    """Import the Agentmail SDK only when sending email at runtime."""
    from agentmail import AgentMail

    return AgentMail


def _send_message_sync(*, settings: SignalsSettings, request: AgentmailSendMessageRequest) -> Any:
    """Synchronously call Agentmail to send the completion email."""
    AgentMail = _import_agentmail()
    client = AgentMail(api_key=settings.agentmail_api_key.get_secret_value())
    send_kwargs: dict[str, object] = {
        "inbox_id": request.inbox_id,
        "to": request.to,
        "subject": request.subject,
        "text": request.text,
        "html": request.html,
    }
    if request.cc:
        send_kwargs["cc"] = request.cc
    if request.bcc:
        send_kwargs["bcc"] = request.bcc
    if request.reply_to:
        send_kwargs["reply_to"] = request.reply_to
    if request.labels:
        send_kwargs["labels"] = request.labels
    return client.inboxes.messages.send(**send_kwargs)


async def send_message(*, settings: SignalsSettings, request: AgentmailSendMessageRequest) -> AgentmailSendMessageResponse:
    """Send a completion email through Agentmail and normalize the response metadata."""
    response = await asyncio.to_thread(_send_message_sync, settings=settings, request=request)
    message_id = _read_provider_value(source=response, field_names=("message_id", "id"))
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("Agentmail did not return a message identifier.")
    thread_id = _read_provider_value(source=response, field_names=("thread_id",))
    return AgentmailSendMessageResponse(message_id=message_id, thread_id=thread_id)
