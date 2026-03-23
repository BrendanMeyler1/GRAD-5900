import sqlite3
import re
from pathlib import Path
from typing import Dict, List, Optional


class StructuredFactsStore:
    STOPWORDS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does",
        "for", "from", "how", "in", "is", "it", "of", "on", "or", "the", "to",
        "was", "were", "what", "when", "which", "who", "why",
    }
    TOKEN_ALIASES = {
        "revenue": {"revenue", "revenues", "sales"},
        "sales": {"sales", "revenue", "revenues"},
        "automotive": {"automotive", "vehicle"},
        "energy": {"energy", "storage"},
        "storage": {"storage", "energy"},
        "regulatory": {"regulatory", "credit", "credits"},
        "credits": {"credits", "credit", "regulatory"},
        "fiscal": {"fiscal", "year"},
        "year": {"year", "fiscal"},
    }

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS financial_facts (
                    fact_id TEXT PRIMARY KEY,
                    file_hash TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    section_index INTEGER NOT NULL,
                    page_label TEXT,
                    metric_key TEXT NOT NULL,
                    metric_label TEXT NOT NULL,
                    period TEXT,
                    value_text TEXT NOT NULL,
                    value_numeric REAL,
                    normalized_value REAL,
                    unit TEXT,
                    currency TEXT,
                    evidence_text TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_financial_facts_source_name
                    ON financial_facts(source_name);

                CREATE INDEX IF NOT EXISTS idx_financial_facts_file_hash
                    ON financial_facts(file_hash);

                CREATE INDEX IF NOT EXISTS idx_financial_facts_metric_key
                    ON financial_facts(metric_key);
                """
            )

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM financial_facts")
            conn.commit()

    def replace_document_facts(self, source_name: str, file_hash: str, facts: List[Dict]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM financial_facts WHERE file_hash = ?", (file_hash,))
            for fact in facts:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO financial_facts (
                        fact_id, file_hash, source_name, section_index, page_label,
                        metric_key, metric_label, period, value_text, value_numeric,
                        normalized_value, unit, currency, evidence_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact["fact_id"],
                        file_hash,
                        source_name,
                        fact["section_index"],
                        fact.get("page_label"),
                        fact["metric_key"],
                        fact["metric_label"],
                        fact.get("period"),
                        fact["value_text"],
                        fact.get("value_numeric"),
                        fact.get("normalized_value"),
                        fact.get("unit"),
                        fact.get("currency"),
                        fact["evidence_text"],
                    ),
                )
            conn.commit()

    def delete_document_facts(self, file_hash: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM financial_facts WHERE file_hash = ?", (file_hash,))
            conn.commit()
            return cursor.rowcount

    def summary(self) -> Dict[str, int]:
        with self._connect() as conn:
            num_facts = conn.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0]
            num_documents = conn.execute(
                "SELECT COUNT(DISTINCT file_hash) FROM financial_facts"
            ).fetchone()[0]
        return {"num_facts": num_facts, "num_documents": num_documents}

    def document_fact_count(
        self,
        source_name: Optional[str] = None,
        file_hash: Optional[str] = None,
    ) -> int:
        query = "SELECT COUNT(*) FROM financial_facts"
        params = []
        clauses = []
        if source_name:
            clauses.append("source_name = ?")
            params.append(source_name)
        if file_hash:
            clauses.append("file_hash = ?")
            params.append(file_hash)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        with self._connect() as conn:
            return conn.execute(query, params).fetchone()[0]

    def list_document_facts(
        self,
        source_name: Optional[str] = None,
        file_hash: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict]:
        query = """
            SELECT fact_id, file_hash, source_name, section_index, page_label,
                   metric_key, metric_label, period, value_text, value_numeric,
                   normalized_value, unit, currency, evidence_text
            FROM financial_facts
        """
        params = []
        clauses = []
        if source_name:
            clauses.append("source_name = ?")
            params.append(source_name)
        if file_hash:
            clauses.append("file_hash = ?")
            params.append(file_hash)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY source_name ASC, section_index ASC, metric_label ASC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            {
                "fact_id": row[0],
                "file_hash": row[1],
                "source_name": row[2],
                "section_index": row[3],
                "page_label": row[4],
                "metric_key": row[5],
                "metric_label": row[6],
                "period": row[7],
                "value_text": row[8],
                "value_numeric": row[9],
                "normalized_value": row[10],
                "unit": row[11],
                "currency": row[12],
                "evidence_text": row[13],
            }
            for row in rows
        ]

    def search_facts(
        self,
        query: str,
        source_names: Optional[List[str]] = None,
        limit: int = 6,
    ) -> List[Dict]:
        tokens = [
            token
            for token in re.findall(r"[a-zA-Z0-9]+", query.lower())
            if token not in self.STOPWORDS and len(token) > 1
        ]

        query_sql = """
            SELECT fact_id, file_hash, source_name, section_index, page_label,
                   metric_key, metric_label, period, value_text, value_numeric,
                   normalized_value, unit, currency, evidence_text
            FROM financial_facts
        """
        params: List[str] = []
        if source_names:
            placeholders = ",".join("?" for _ in source_names)
            query_sql += f" WHERE source_name IN ({placeholders})"
            params.extend(source_names)

        with self._connect() as conn:
            rows = conn.execute(query_sql, params).fetchall()

        facts = [
            {
                "fact_id": row[0],
                "file_hash": row[1],
                "source_name": row[2],
                "section_index": row[3],
                "page_label": row[4],
                "metric_key": row[5],
                "metric_label": row[6],
                "period": row[7],
                "value_text": row[8],
                "value_numeric": row[9],
                "normalized_value": row[10],
                "unit": row[11],
                "currency": row[12],
                "evidence_text": row[13],
            }
            for row in rows
        ]

        return self.rank_facts(query=query, facts=facts, limit=limit)

    def rank_facts(
        self,
        query: str,
        facts: List[Dict],
        limit: int = 6,
    ) -> List[Dict]:
        tokens = [
            token
            for token in re.findall(r"[a-zA-Z0-9]+", query.lower())
            if token not in self.STOPWORDS and len(token) > 1
        ]
        expanded_tokens = self._expand_tokens(tokens)

        ranked_rows = []
        lowered_query = query.lower()
        for fact in facts:
            score = self._score_fact_match(
                fact,
                tokens=tokens,
                expanded_tokens=expanded_tokens,
                lowered_query=lowered_query,
            )
            if score <= 0:
                continue
            enriched = dict(fact)
            enriched["match_score"] = score
            ranked_rows.append(enriched)

        ranked_rows.sort(
            key=lambda fact: (
                -fact["match_score"],
                fact.get("source_name", ""),
                fact.get("section_index", 0),
                fact.get("metric_label", ""),
            )
        )
        return ranked_rows[:limit]

    def _expand_tokens(self, tokens: List[str]) -> List[str]:
        expanded = []
        for token in tokens:
            expanded.append(token)
            expanded.extend(sorted(self.TOKEN_ALIASES.get(token, set())))
        return list(dict.fromkeys(expanded))

    def _score_fact_match(
        self,
        fact: Dict,
        tokens: List[str],
        expanded_tokens: List[str],
        lowered_query: str,
    ) -> int:
        metric_text = f"{fact.get('metric_key', '')} {fact.get('metric_label', '')}".lower()
        period_text = (fact.get("period") or "").lower()
        source_text = (fact.get("source_name") or "").lower()
        evidence_text = (fact.get("evidence_text") or "").lower()
        value_text = (fact.get("value_text") or "").lower()

        score = 0
        if fact.get("metric_label", "").lower() in lowered_query:
            score += 8
        if fact.get("metric_key", "").replace("_", " ") in lowered_query:
            score += 8

        for token in tokens:
            if token in metric_text:
                score += 5
            elif token in period_text:
                score += 4
            elif token in source_text:
                score += 3
            elif token in value_text:
                score += 2
            elif token in evidence_text:
                score += 2

        for token in expanded_tokens:
            if token in metric_text:
                score += 3
            elif token in evidence_text:
                score += 1

        if "2024" in lowered_query and ("2024" in period_text or "2024" in evidence_text):
            score += 2

        if any(term in lowered_query for term in ("relationship", "relationships", "connect", "across")):
            if any(term in evidence_text for term in ("automotive", "energy", "regulatory", "revenue", "sales")):
                score += 2

        return score
