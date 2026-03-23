"""
Shared OpenAI client singletons.

Issue #9 fix: All modules share a single OpenAI() and AsyncOpenAI()
connection pool instead of each class creating its own.
"""
from openai import OpenAI, AsyncOpenAI
from backend.core.config import settings

# Shared synchronous client
openai_client = OpenAI(api_key=settings.openai_api_key)

# Shared async client (used by graph extractor)
async_openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
