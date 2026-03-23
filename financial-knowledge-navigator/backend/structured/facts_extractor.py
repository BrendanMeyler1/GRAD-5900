import hashlib
import re
from typing import Dict, List, Optional


class StructuredFactsExtractor:
    METRIC_PATTERNS = [
        ("revenue", "Revenue", re.compile(r"\b(total\s+revenues?|revenues?|sales)\b", re.IGNORECASE)),
        ("net_income", "Net income", re.compile(r"\b(net\s+income|net\s+earnings|net\s+profit)\b", re.IGNORECASE)),
        ("operating_income", "Operating income", re.compile(r"\b(operating\s+income|income\s+from\s+operations)\b", re.IGNORECASE)),
        ("gross_margin", "Gross margin", re.compile(r"\b(gross\s+margin)\b", re.IGNORECASE)),
        ("operating_margin", "Operating margin", re.compile(r"\b(operating\s+margin)\b", re.IGNORECASE)),
        ("free_cash_flow", "Free cash flow", re.compile(r"\b(free\s+cash\s+flow)\b", re.IGNORECASE)),
        ("operating_cash_flow", "Operating cash flow", re.compile(r"\b(operating\s+cash\s+flow|cash\s+flow\s+from\s+operations)\b", re.IGNORECASE)),
        ("cash", "Cash and cash equivalents", re.compile(r"\b(cash\s+and\s+cash\s+equivalents|cash\s+equivalents)\b", re.IGNORECASE)),
        ("total_assets", "Total assets", re.compile(r"\b(total\s+assets)\b", re.IGNORECASE)),
        ("total_liabilities", "Total liabilities", re.compile(r"\b(total\s+liabilities)\b", re.IGNORECASE)),
        ("capex", "Capital expenditures", re.compile(r"\b(capex|capital\s+expenditures?)\b", re.IGNORECASE)),
        ("automotive_sales", "Automotive sales", re.compile(r"\b(automotive\s+sales|automotive\s+revenues?)\b", re.IGNORECASE)),
        ("energy_generation_storage", "Energy generation and storage", re.compile(r"\b(energy\s+generation\s+and\s+storage(?:\s+revenues?)?|energy\s+storage(?:\s+revenues?)?|storage\s+revenues?)\b", re.IGNORECASE)),
        ("regulatory_credits", "Regulatory credits", re.compile(r"\b(regulatory\s+credits?)\b", re.IGNORECASE)),
        ("diluted_eps", "Diluted EPS", re.compile(r"\b(diluted\s+eps|earnings\s+per\s+share|eps)\b", re.IGNORECASE)),
    ]

    VALUE_PATTERN = re.compile(
        r"(?P<currency>\$)?(?P<number>\d{1,3}(?:,\d{3})*(?:\.\d+)?)"
        r"(?:\s*(?P<unit>billion|million|thousand|percent|%|basis points|bps))?",
        re.IGNORECASE,
    )

    PERIOD_PATTERNS = [
        re.compile(r"\b(Q[1-4]\s+20\d{2})\b", re.IGNORECASE),
        re.compile(r"\b(FY\s*20\d{2})\b", re.IGNORECASE),
        re.compile(r"\b(year ended [A-Za-z]+\s+\d{1,2},\s+20\d{2})\b", re.IGNORECASE),
        re.compile(r"\b(quarter ended [A-Za-z]+\s+\d{1,2},\s+20\d{2})\b", re.IGNORECASE),
        re.compile(r"\b(20\d{2})\b"),
    ]

    def extract_from_section(
        self,
        section_text: str,
        source_name: str,
        file_hash: str,
        section_index: int,
    ) -> List[Dict]:
        if not section_text.strip():
            return []

        section_period = self._extract_period(section_text)
        facts: List[Dict] = []
        seen_ids = set()

        for line in self._iter_candidate_lines(section_text):
            line_period = self._extract_period(line) or section_period
            for metric_key, metric_label, metric_pattern in self.METRIC_PATTERNS:
                if not metric_pattern.search(line):
                    continue
                value_match = self._best_value_match(line, metric_pattern)
                if value_match is None:
                    continue

                value_text = value_match.group(0).strip()
                currency = "$" if value_match.group("currency") else ""
                unit = (value_match.group("unit") or "").strip().lower()
                numeric_value = self._parse_number(value_match.group("number"))
                normalized_value = self._normalize_value(numeric_value, unit)
                page_label = self._extract_page_label(line) or self._extract_page_label(section_text)

                fact = {
                    "fact_id": self._fact_id(
                        file_hash=file_hash,
                        section_index=section_index,
                        metric_key=metric_key,
                        evidence_text=line,
                        value_text=value_text,
                    ),
                    "file_hash": file_hash,
                    "source_name": source_name,
                    "section_index": section_index,
                    "page_label": page_label,
                    "metric_key": metric_key,
                    "metric_label": metric_label,
                    "period": line_period,
                    "value_text": value_text,
                    "value_numeric": numeric_value,
                    "normalized_value": normalized_value,
                    "unit": unit or None,
                    "currency": currency or None,
                    "evidence_text": line,
                }
                if fact["fact_id"] in seen_ids:
                    continue
                seen_ids.add(fact["fact_id"])
                facts.append(fact)

        return facts

    def _iter_candidate_lines(self, section_text: str) -> List[str]:
        lines = []
        for raw_line in section_text.splitlines():
            cleaned = " ".join(raw_line.split())
            if len(cleaned) < 12:
                continue
            lines.append(cleaned[:500])
        return lines

    def _best_value_match(self, line: str, metric_pattern: re.Pattern) -> Optional[re.Match]:
        metric_match = metric_pattern.search(line)
        if metric_match is None:
            return None

        matches = list(self.VALUE_PATTERN.finditer(line))
        if not matches:
            return None

        metric_index = metric_match.start()
        after_metric = [
            match for match in matches
            if match.start() >= metric_index and self._valid_value_match(match)
        ]
        if after_metric:
            return after_metric[0]

        before_metric = [
            match for match in matches
            if match.start() < metric_index and self._valid_value_match(match)
        ]
        if before_metric:
            return before_metric[-1]
        return None

    def _valid_value_match(self, match: re.Match) -> bool:
        unit = (match.group("unit") or "").lower()
        if unit in {"percent", "%", "basis points", "bps"}:
            return True
        return bool(match.group("currency") or unit)

    def _parse_number(self, raw_number: str) -> float:
        return float(raw_number.replace(",", ""))

    def _normalize_value(self, numeric_value: float, unit: str) -> float:
        scales = {
            "billion": 1_000_000_000.0,
            "million": 1_000_000.0,
            "thousand": 1_000.0,
            "basis points": 0.0001,
            "bps": 0.0001,
        }
        if unit in {"percent", "%"}:
            return numeric_value / 100.0
        return numeric_value * scales.get(unit, 1.0)

    def _extract_period(self, text: str) -> Optional[str]:
        for pattern in self.PERIOD_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1).strip()
        return None

    def _extract_page_label(self, text: str) -> Optional[str]:
        match = re.search(r"\[(Page\s+\d+)\]", text, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _fact_id(
        self,
        file_hash: str,
        section_index: int,
        metric_key: str,
        evidence_text: str,
        value_text: str,
    ) -> str:
        digest = hashlib.sha256(
            f"{file_hash}|{section_index}|{metric_key}|{evidence_text}|{value_text}".encode("utf-8")
        ).hexdigest()
        return digest[:24]
