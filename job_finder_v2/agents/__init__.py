"""
Agents for job_finder_v2.

Multi-agent architecture: one Orchestrator (manager) delegates to Workers
via Claude tool use. Each worker is self-contained and uses the LLM client
+ MCP tools + DB store as needed.
"""

from agents.email_tracker import EmailTracker
from agents.form_filler import FormFillerAgent
from agents.job_scout import JobScout, ScoredJob
from agents.orchestrator import Orchestrator
from agents.profile_builder import ProfileBuilder
from agents.resume_writer import ResumeWriter

__all__ = [
    "EmailTracker",
    "FormFillerAgent",
    "JobScout",
    "ScoredJob",
    "Orchestrator",
    "ProfileBuilder",
    "ResumeWriter",
]
