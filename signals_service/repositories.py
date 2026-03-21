from __future__ import annotations

from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorDatabase

from models import Research, ResearchStatus, WhitelistEntryStatus, WhitelistedEmail, WhitelistedEmailDomain
from signals_service.database import (
    get_researches_collection,
    get_whitelisted_email_domains_collection,
    get_whitelisted_emails_collection,
)


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(UTC)


def _strip_mongo_id(*, document: dict | None) -> dict | None:
    """Remove MongoDB's internal `_id` field before validating against business models."""
    if document is None:
        return None
    normalized_document = dict(document)
    normalized_document.pop("_id", None)
    return normalized_document


async def find_active_whitelisted_email(*, database: AsyncIOMotorDatabase, email: str) -> WhitelistedEmail | None:
    """Look up an explicitly approved active email address."""
    document = await get_whitelisted_emails_collection(database=database).find_one(
        {
            "email": email,
            "status": WhitelistEntryStatus.ACTIVE.value,
        }
    )
    normalized_document = _strip_mongo_id(document=document)
    return WhitelistedEmail.model_validate(normalized_document) if normalized_document else None


async def find_active_whitelisted_email_domain(
    *,
    database: AsyncIOMotorDatabase,
    domain: str,
) -> WhitelistedEmailDomain | None:
    """Look up an explicitly approved active corporate domain."""
    document = await get_whitelisted_email_domains_collection(database=database).find_one(
        {
            "domain": domain,
            "status": WhitelistEntryStatus.ACTIVE.value,
        }
    )
    normalized_document = _strip_mongo_id(document=document)
    return WhitelistedEmailDomain.model_validate(normalized_document) if normalized_document else None


async def insert_research(*, database: AsyncIOMotorDatabase, research: Research) -> Research:
    """Insert a newly created research workflow document."""
    await get_researches_collection(database=database).insert_one(research.model_dump())
    return research


async def get_research(*, database: AsyncIOMotorDatabase, research_id: str) -> Research | None:
    """Fetch a research workflow document by its business identifier."""
    document = await get_researches_collection(database=database).find_one({"research_id": research_id})
    normalized_document = _strip_mongo_id(document=document)
    return Research.model_validate(normalized_document) if normalized_document else None


async def replace_research(*, database: AsyncIOMotorDatabase, research: Research) -> Research:
    """Persist an updated research workflow document with optimistic versioning."""
    updated_research = research.model_copy(
        update={
            "updated_at": _utc_now(),
            "version": research.version + 1,
        },
        deep=True,
    )
    replace_result = await get_researches_collection(database=database).replace_one(
        {
            "research_id": research.research_id,
            "version": research.version,
        },
        updated_research.model_dump(),
    )
    if replace_result.matched_count != 1:
        raise RuntimeError(f"Failed to persist research {research.research_id}; the document version changed unexpectedly.")
    return updated_research


async def list_queued_researches(*, database: AsyncIOMotorDatabase, limit: int) -> list[Research]:
    """Fetch queued research workflows that still need Deep Research submission."""
    cursor = (
        get_researches_collection(database=database)
        .find({"status": ResearchStatus.RESEARCH_QUEUED.value})
        .sort("created_at", 1)
        .limit(limit)
    )
    return [Research.model_validate(_strip_mongo_id(document=document)) async for document in cursor]


async def list_researches_due_for_poll(
    *,
    database: AsyncIOMotorDatabase,
    now: datetime,
    limit: int,
) -> list[Research]:
    """Fetch active research workflows whose Gemini interaction should be polled now."""
    cursor = (
        get_researches_collection(database=database)
        .find(
            {
                "status": ResearchStatus.RESEARCH_IN_PROGRESS.value,
                "$or": [
                    {"gemini_interaction.next_poll_at": {"$lte": now}},
                    {"gemini_interaction.next_poll_at": None},
                    {"gemini_interaction.next_poll_at": {"$exists": False}},
                ],
            }
        )
        .sort("status_updated_at", 1)
        .limit(limit)
    )
    return [Research.model_validate(_strip_mongo_id(document=document)) async for document in cursor]
