"""
agents/orchestrator.py — Manager agent.

The Orchestrator receives natural-language goals from the user (via the
chat endpoint) and delegates to worker agents using Claude tool use.
It is the only agent that the chat layer talks to directly.

Tool set available to the model:
    - search_jobs(query, location, limit)
    - tailor_resume(job_id)
    - run_shadow_application(job_id)
    - get_user_profile()
    - update_profile(fields)
    - get_applications(status?)
    - get_job_detail(job_id)
    - sync_email(since_days?)

The orchestrator runs a short tool-use loop: it calls the model with the
user goal and tool definitions; if the model picks a tool, we execute it
and feed the result back; repeat until the model returns a final text
answer or we hit the loop cap (10 iterations to prevent runaway cost).

Streaming is handled by a separate path in api/routes/chat.py — the
orchestrator is used for the "execute actions" flavour of chat, while
pure conversational turns stream directly from LLMClient.stream().
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from db.store import JobFilters, Store
from llm.client import LLMClient, ToolUseResult, load_prompt

log = logging.getLogger(__name__)


# --- Tool schemas (Anthropic tool use format) --------------------------------

ORCHESTRATOR_TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_jobs",
        "description": (
            "Search for job listings and score them against the user's profile. "
            "Runs scrapers (JSearch, Greenhouse, Lever), deduplicates, and "
            "returns scored results sorted by fit score. Costs ~1 request + "
            "1 Claude call per result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Role/keywords, e.g. 'python backend engineer'",
                },
                "location": {
                    "type": "string",
                    "description": "City + state or 'remote'. Empty string for anywhere.",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (typical: 10-25)",
                    "default": 15,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "tailor_resume",
        "description": (
            "Generate a tailored resume and cover letter for a specific job. "
            "Creates an Application record in 'pending' state. Costs 2 Claude calls."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "run_shadow_application",
        "description": (
            "Shadow-apply to a job: tailor documents, fill the form, capture "
            "screenshots, STOP before submitting. Takes 1-3 minutes. "
            "ONLY call this after confirming with the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "get_user_profile",
        "description": "Return the user's complete profile (name, contact, education, experience, skills, preferences).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_profile",
        "description": "Update top-level profile fields. Accepts any subset of profile columns.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "description": "Map of profile field name -> new value",
                },
            },
            "required": ["fields"],
        },
    },
    {
        "name": "get_applications",
        "description": "List applications, optionally filtered by status (pending|shadow_review|submitted|rejected|offer_received|aborted).",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
            },
        },
    },
    {
        "name": "get_job_detail",
        "description": "Return full detail for a specific job listing (description, fit, strengths, gaps).",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "sync_email",
        "description": "Scan Outlook inbox for recent recruiter emails and classify them. Updates application statuses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "since_days": {"type": "integer", "default": 7},
            },
        },
    },
]


@dataclass
class OrchestratorResult:
    """Outcome of one orchestrator turn."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)  # "jobs_updated", etc.


class Orchestrator:
    """
    Manager agent. Receives user goals, delegates to workers via tool use,
    returns a synthesized text reply.

    Args:
        store: DB Store for profile/jobs/applications lookups.
        llm:   LLM client.
        workers: dict of worker instances: {"job_scout": ..., "resume_writer": ...,
                 "form_filler": ..., "email_tracker": ..., "pipeline": ...}
    """

    def __init__(
        self,
        store: Store,
        llm: LLMClient | None = None,
        workers: dict[str, Any] | None = None,
        max_iterations: int = 8,
    ) -> None:
        self.store = store
        self.llm = llm or LLMClient()
        self.workers = workers or {}
        self.max_iterations = max_iterations
        self._prompt = load_prompt("orchestrator")

    async def handle(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
        live_context: str = "",
    ) -> OrchestratorResult:
        """
        Run a single orchestrator turn.

        Args:
            user_message: The latest user message.
            history: Prior conversation turns as Anthropic-format messages.
            live_context: A pre-computed context block (profile, pending reviews,
                          recent jobs) that should be prepended to the system prompt.

        Returns:
            OrchestratorResult with the final assistant text and any side effects
            the UI should react to (e.g. "jobs_updated" → invalidate job cache).
        """
        system = self._prompt
        if live_context:
            system = f"{self._prompt}\n\n--- LIVE CONTEXT ---\n{live_context}"

        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": user_message})

        side_effects: list[str] = []
        tool_calls_log: list[dict[str, Any]] = []

        for iteration in range(self.max_iterations):
            response = await self.llm.chat(
                messages=messages,
                system=system,
                tools=ORCHESTRATOR_TOOLS,
                max_tokens=4096,
            )

            # Plain text reply → we're done
            if isinstance(response, str):
                log.info(
                    "orchestrator.done",
                    extra={"iterations": iteration + 1, "tool_calls": len(tool_calls_log)},
                )
                return OrchestratorResult(
                    text=response.strip(),
                    tool_calls=tool_calls_log,
                    side_effects=side_effects,
                )

            # Tool call → execute and feed result back
            tool_result = await self._execute_tool(response)
            tool_calls_log.append(
                {
                    "tool": response.tool_name,
                    "input": response.tool_input,
                    "output_preview": str(tool_result)[:200],
                }
            )

            side_effect = _TOOL_SIDE_EFFECTS.get(response.tool_name)
            if side_effect and side_effect not in side_effects:
                side_effects.append(side_effect)

            # Append assistant's tool_use block + user's tool_result block
            messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": response.tool_use_id,
                            "name": response.tool_name,
                            "input": response.tool_input,
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": response.tool_use_id,
                            "content": json.dumps(tool_result, default=_json_default)[:20000],
                        }
                    ],
                }
            )

        # Safety cap hit
        log.warning("orchestrator.max_iterations_hit")
        return OrchestratorResult(
            text="I've been working on that but I'm going to pause here — "
            "let me know what you'd like to focus on next.",
            tool_calls=tool_calls_log,
            side_effects=side_effects,
        )

    # --- tool dispatch ----------------------------------------------------

    async def _execute_tool(self, call: ToolUseResult) -> Any:
        """Dispatch a tool call to the appropriate worker and return its result."""
        name = call.tool_name
        args = call.tool_input or {}
        log.info(
            "orchestrator.tool_call",
            extra={"tool": name, "args_keys": list(args.keys())},
        )

        try:
            if name == "search_jobs":
                scout = self.workers.get("job_scout")
                if scout is None:
                    return {"error": "job_scout worker not available"}
                scored = await scout.discover(
                    query=args.get("query", ""),
                    location=args.get("location", ""),
                    limit=int(args.get("limit", 15)),
                )
                return [
                    {
                        "id": s.listing.id,
                        "title": s.listing.title,
                        "company": s.listing.company,
                        "location": s.listing.location,
                        "remote_ok": s.listing.remote_ok,
                        "apply_url": s.listing.apply_url,
                        "fit_score": s.score,
                        "fit_summary": s.summary,
                        "strengths": s.strengths,
                        "gaps": s.gaps,
                        "interview_likelihood": s.interview_likelihood,
                    }
                    for s in scored
                ]

            if name == "tailor_resume":
                job_id = args.get("job_id")
                pipeline = self.workers.get("pipeline")
                if pipeline is None:
                    return {"error": "pipeline worker not available"}
                # Just the document-generation half of the pipeline
                app = await pipeline.tailor_only(job_id)
                return {
                    "app_id": app.id,
                    "status": app.status,
                    "resume_path": app.resume_tailored_path,
                }

            if name == "run_shadow_application":
                job_id = args.get("job_id")
                pipeline = self.workers.get("pipeline")
                if pipeline is None:
                    return {"error": "pipeline worker not available"}
                app = await pipeline.run_application(job_id=job_id, mode="shadow")
                return {
                    "app_id": app.id,
                    "status": app.status,
                    "screenshot_count": len(app.shadow_screenshots or []),
                }

            if name == "get_user_profile":
                profile = self.store.get_full_profile()
                return json.loads(profile.model_dump_json())

            if name == "update_profile":
                fields = args.get("fields") or {}
                updated = self.store.upsert_profile(fields)
                return json.loads(updated.model_dump_json())

            if name == "get_applications":
                status = args.get("status")
                apps = self.store.list_applications(status=status)
                return [
                    {
                        "id": a.id,
                        "job_id": a.job_id,
                        "status": a.status,
                        "company": a.job.company if a.job else None,
                        "title": a.job.title if a.job else None,
                        "created_at": a.created_at,
                    }
                    for a in apps
                ]

            if name == "get_job_detail":
                job_id = args.get("job_id")
                job = self.store.get_job(job_id)
                if job is None:
                    return {"error": f"job {job_id} not found"}
                return json.loads(job.model_dump_json())

            if name == "sync_email":
                tracker = self.workers.get("email_tracker")
                if tracker is None:
                    return {"error": "email_tracker not available"}
                events = await tracker.sync(since_days=int(args.get("since_days", 7)))
                return [
                    {
                        "app_id": e.app_id,
                        "company": e.company,
                        "category": e.category,
                        "summary": e.summary,
                        "urgency": e.urgency,
                        "action_needed": e.action_needed,
                    }
                    for e in events
                ]

            return {"error": f"unknown tool {name!r}"}

        except Exception as exc:  # noqa: BLE001 — fed back to the LLM
            log.exception(
                "orchestrator.tool_error",
                extra={"tool": name, "error": str(exc)},
            )
            return {"error": str(exc), "tool": name}


_TOOL_SIDE_EFFECTS: dict[str, str] = {
    "search_jobs": "jobs_updated",
    "tailor_resume": "applications_updated",
    "run_shadow_application": "applications_updated",
    "update_profile": "profile_updated",
    "sync_email": "email_events_updated",
}


def _json_default(o: Any) -> Any:
    """JSON fallback for Pydantic models / datetime / other exotics."""
    if hasattr(o, "model_dump"):
        return o.model_dump()
    if hasattr(o, "__dict__"):
        return o.__dict__
    return str(o)
