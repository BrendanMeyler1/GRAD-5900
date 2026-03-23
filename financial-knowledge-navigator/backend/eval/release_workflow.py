import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from backend.core.config import settings


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReleaseWorkflowStore:
    def __init__(self, db_path: Optional[str] = None, reports_dir: str = "data/reports"):
        self.db_path = Path(db_path or settings.release_workflow_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS release_decisions (
                    decision_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    selected_mode TEXT,
                    overall_ready INTEGER NOT NULL DEFAULT 0,
                    best_candidate_mode TEXT,
                    note TEXT,
                    gate_result_json TEXT NOT NULL,
                    offline_summary_json TEXT,
                    online_summary_json TEXT,
                    thresholds_json TEXT,
                    blockers_json TEXT,
                    deployable_modes_json TEXT
                )
                """
            )

    def clear(self) -> Dict[str, int]:
        self._init_db()
        with self._connect() as conn:
            existing_rows = conn.execute("SELECT COUNT(*) FROM release_decisions").fetchone()[0]
            conn.execute("DELETE FROM release_decisions")
            conn.commit()
        return {"release_decisions_reset": existing_rows}

    def record_decision(
        self,
        decision: str,
        gate_result: Dict[str, Any],
        offline_eval_results: Optional[Dict[str, Any]] = None,
        online_summary: Optional[Dict[str, Any]] = None,
        note: str = "",
        selected_mode: Optional[str] = None,
    ) -> str:
        decision_id = uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO release_decisions (
                    decision_id, created_at, decision, selected_mode, overall_ready,
                    best_candidate_mode, note, gate_result_json, offline_summary_json,
                    online_summary_json, thresholds_json, blockers_json, deployable_modes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    _utc_now(),
                    decision,
                    selected_mode,
                    int(bool(gate_result.get("overall_ready"))),
                    gate_result.get("best_candidate_mode"),
                    note or "",
                    json.dumps(gate_result, ensure_ascii=False),
                    json.dumps((offline_eval_results or {}).get("summary", {}), ensure_ascii=False),
                    json.dumps(online_summary or {}, ensure_ascii=False),
                    json.dumps(gate_result.get("thresholds", {}), ensure_ascii=False),
                    json.dumps(gate_result.get("blockers", []), ensure_ascii=False),
                    json.dumps(gate_result.get("deployable_modes", []), ensure_ascii=False),
                ),
            )
            conn.commit()
        return decision_id

    def list_decisions(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT decision_id, created_at, decision, selected_mode, overall_ready,
                       best_candidate_mode, note, gate_result_json, offline_summary_json,
                       online_summary_json, thresholds_json, blockers_json, deployable_modes_json
                FROM release_decisions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        decisions: List[Dict[str, Any]] = []
        for row in rows:
            decisions.append(
                {
                    "decision_id": row[0],
                    "created_at": row[1],
                    "decision": row[2],
                    "selected_mode": row[3],
                    "overall_ready": bool(row[4]),
                    "best_candidate_mode": row[5],
                    "note": row[6] or "",
                    "gate_result": json.loads(row[7] or "{}"),
                    "offline_summary": json.loads(row[8] or "{}"),
                    "online_summary": json.loads(row[9] or "{}"),
                    "thresholds": json.loads(row[10] or "{}"),
                    "blockers": json.loads(row[11] or "[]"),
                    "deployable_modes": json.loads(row[12] or "[]"),
                }
            )
        return decisions

    def latest_decision(self) -> Optional[Dict[str, Any]]:
        decisions = self.list_decisions(limit=1)
        return decisions[0] if decisions else None

    def summary(self) -> Dict[str, Any]:
        decisions = self.list_decisions(limit=100)
        if not decisions:
            return {
                "total_decisions": 0,
                "promotions": 0,
                "holds": 0,
                "rollbacks": 0,
                "latest_decision": None,
                "latest_mode": None,
                "latest_ready": False,
                "latest_created_at": None,
            }

        latest = decisions[0]
        return {
            "total_decisions": len(decisions),
            "promotions": sum(1 for row in decisions if row["decision"] == "promote"),
            "holds": sum(1 for row in decisions if row["decision"] == "hold"),
            "rollbacks": sum(1 for row in decisions if row["decision"] == "rollback"),
            "latest_decision": latest["decision"],
            "latest_mode": latest.get("selected_mode") or latest.get("best_candidate_mode"),
            "latest_ready": latest.get("overall_ready", False),
            "latest_created_at": latest.get("created_at"),
        }

    def export_markdown_report(self, decision_id: str) -> str:
        decision = next(
            (row for row in self.list_decisions(limit=200) if row["decision_id"] == decision_id),
            None,
        )
        if decision is None:
            raise ValueError(f"Unknown release decision id: {decision_id}")

        file_name = f"release_workflow_{decision['created_at'].replace(':', '').replace('-', '')}.md"
        path = self.reports_dir / file_name

        lines = [
            "# Deployment Gate Release Decision",
            "",
            f"Generated: {decision['created_at']}",
            "",
            "## Decision",
            "",
            f"- Decision: {decision['decision']}",
            f"- Selected mode: {decision.get('selected_mode') or 'N/A'}",
            f"- Best candidate mode: {decision.get('best_candidate_mode') or 'N/A'}",
            f"- Gate ready: {decision.get('overall_ready')}",
        ]

        if decision.get("note"):
            lines.extend(["", "## Note", "", decision["note"]])

        lines.extend(["", "## Blockers", ""])
        blockers = decision.get("blockers") or []
        if blockers:
            for blocker in blockers:
                lines.append(f"- {blocker}")
        else:
            lines.append("- None")

        lines.extend(["", "## Thresholds", ""])
        for key, value in (decision.get("thresholds") or {}).items():
            lines.append(f"- {key}: {value}")

        lines.extend(["", "## Online Summary", ""])
        for key, value in (decision.get("online_summary") or {}).items():
            lines.append(f"- {key}: {value}")

        lines.extend(["", "## Offline Summary by Mode", ""])
        offline_summary = decision.get("offline_summary") or {}
        if not offline_summary:
            lines.append("- No offline evaluation summary was attached.")
        else:
            for mode, mode_summary in offline_summary.items():
                lines.append(f"### {mode.upper()}")
                lines.append("")
                for key, value in mode_summary.items():
                    lines.append(f"- {key}: {value}")
                lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return str(path)
