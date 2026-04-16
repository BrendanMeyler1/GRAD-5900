"""
memory/application_patterns.py — Per-company form memory.

Thin service over the `application_memory` table. When the form filler
finishes an application, it writes what worked and what didn't. The next
time we apply to the same company, the filler reads this memory and
includes it in the Claude instruction — so the agent learns across runs.

Usage:
    patterns = ApplicationPatterns(store)
    note = patterns.get_for("Stripe")
    patterns.record_success("Stripe", ats_type="greenhouse",
                            form_notes="Cover letter is a textarea, not file.")
"""

from __future__ import annotations

import logging

from db.store import AppMemory, Store

log = logging.getLogger(__name__)


class ApplicationPatterns:
    """CRUD facade around application_memory."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def get_for(self, company: str) -> AppMemory | None:
        """Return any stored memory for this company, or None."""
        if not company:
            return None
        return self.store.get_app_memory(company)

    def record_success(
        self,
        company: str,
        ats_type: str | None = None,
        form_notes: str | None = None,
        what_worked: str | None = None,
    ) -> AppMemory:
        """Record that an application flow worked for this company."""
        return self.store.upsert_app_memory(
            company=company,
            ats_type=ats_type,
            form_notes=form_notes,
            what_worked=what_worked,
        )

    def record_failure(
        self,
        company: str,
        error: str,
        ats_type: str | None = None,
        form_notes: str | None = None,
    ) -> AppMemory:
        """Record that something broke for this company."""
        return self.store.upsert_app_memory(
            company=company,
            ats_type=ats_type,
            what_failed=error[:500],
            form_notes=form_notes,
        )

    def format_for_prompt(self, company: str) -> str:
        """
        Return a compact human-readable block summarizing what we know
        about this company's application flow, for injection into the
        filler's instruction. Empty string if nothing is known.
        """
        mem = self.get_for(company)
        if mem is None:
            return ""
        lines: list[str] = [f"PRIOR EXPERIENCE WITH {company.upper()}:"]
        if mem.ats_type:
            lines.append(f"  ATS: {mem.ats_type}")
        if mem.what_worked:
            lines.append(f"  Worked previously: {mem.what_worked}")
        if mem.what_failed:
            lines.append(f"  Known issue: {mem.what_failed}")
        if mem.form_notes:
            lines.append(f"  Notes: {mem.form_notes}")
        return "\n".join(lines)
