"""
Memory subsystems for job_finder_v2.

- ConversationMemory: rolling summary of chat history (long-term context
  compression for the LLM).
- ApplicationPatterns: per-company form notes so the filler gets smarter
  over time.
"""

from memory.application_patterns import ApplicationPatterns
from memory.conversation import ConversationMemory

__all__ = ["ApplicationPatterns", "ConversationMemory"]
