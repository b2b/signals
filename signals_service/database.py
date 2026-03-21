from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING


WHITELISTED_EMAILS_COLLECTION_NAME = "whitelisted_emails"
WHITELISTED_EMAIL_DOMAINS_COLLECTION_NAME = "whitelisted_email_domains"
RESEARCHES_COLLECTION_NAME = "researches"


def create_mongo_client(*, mongodb_url: str) -> AsyncIOMotorClient:
    """Create the MongoDB client used by the Signals service."""
    return AsyncIOMotorClient(mongodb_url, tz_aware=True)


def get_database(*, client: AsyncIOMotorClient, database_name: str) -> AsyncIOMotorDatabase:
    """Return the configured MongoDB database."""
    return client[database_name]


def get_whitelisted_emails_collection(*, database: AsyncIOMotorDatabase):
    """Return the collection containing explicitly approved individual emails."""
    return database[WHITELISTED_EMAILS_COLLECTION_NAME]


def get_whitelisted_email_domains_collection(*, database: AsyncIOMotorDatabase):
    """Return the collection containing approved corporate domains."""
    return database[WHITELISTED_EMAIL_DOMAINS_COLLECTION_NAME]


def get_researches_collection(*, database: AsyncIOMotorDatabase):
    """Return the collection storing end-to-end research workflow documents."""
    return database[RESEARCHES_COLLECTION_NAME]


async def ensure_indexes(*, database: AsyncIOMotorDatabase) -> None:
    """Create the indexes required for local development and production operation."""
    await get_whitelisted_emails_collection(database=database).create_index(
        [("email", ASCENDING)],
        unique=True,
        name="whitelisted_email_unique",
    )
    await get_whitelisted_email_domains_collection(database=database).create_index(
        [("domain", ASCENDING)],
        unique=True,
        name="whitelisted_email_domain_unique",
    )
    researches_collection = get_researches_collection(database=database)
    await researches_collection.create_index([("research_id", ASCENDING)], unique=True, name="research_id_unique")
    await researches_collection.create_index([("requester.email", ASCENDING)], name="research_requester_email")
    await researches_collection.create_index([("status", ASCENDING)], name="research_status")
    await researches_collection.create_index(
        [("status", ASCENDING), ("gemini_interaction.next_poll_at", ASCENDING)],
        name="research_poll_schedule",
    )
