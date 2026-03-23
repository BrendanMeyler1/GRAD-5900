#!/usr/bin/env python3
"""
build_financial_starter_dataset.py

Creates a starter dataset for a financial RAG / evaluation project.
Adapted for the Financial Knowledge Navigator.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "META", "JPM", "GS", "XOM", "F", "PTON"]

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

FMP_TRANSCRIPT_DATES_URL = "https://financialmodelingprep.com/stable/earning-call-transcript-dates"
FMP_TRANSCRIPT_URL = "https://financialmodelingprep.com/stable/earning-call-transcript"


@dataclass
class ManifestRecord:
    ticker: str
    company_name: str
    cik: str
    doc_type: str
    source: str
    filing_date: Optional[str]
    period_of_report: Optional[str]
    accession_number: Optional[str]
    form: Optional[str]
    local_path: str
    metadata: Dict[str, Any]


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, default)
    return value.strip() if value else value


class SimpleHTTPClient:
    def __init__(self, user_agent: str, min_interval_seconds: float = 0.25, timeout: int = 30) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "*/*",
                "Connection": "keep-alive",
            }
        )
        self.min_interval_seconds = min_interval_seconds
        self.timeout = timeout
        self._last_request_time = 0.0

    def _sleep_if_needed(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self._sleep_if_needed()
        resp = self.session.get(url, params=params, timeout=self.timeout)
        self._last_request_time = time.time()
        resp.raise_for_status()
        return resp.json()

    def get_text(self, url: str, params: Optional[Dict[str, Any]] = None) -> str:
        self._sleep_if_needed()
        resp = self.session.get(url, params=params, timeout=self.timeout)
        self._last_request_time = time.time()
        resp.raise_for_status()
        return resp.text

    def download_file(self, url: str, dest: Path, params: Optional[Dict[str, Any]] = None) -> None:
        self._sleep_if_needed()
        with self.session.get(url, params=params, timeout=self.timeout, stream=True) as resp:
            self._last_request_time = time.time()
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)


def load_sec_ticker_map(client: SimpleHTTPClient) -> Dict[str, Dict[str, str]]:
    data = client.get_json(SEC_TICKERS_URL)
    out: Dict[str, Dict[str, str]] = {}
    for _, row in data.items():
        ticker = row["ticker"].upper()
        cik = str(row["cik_str"]).zfill(10)
        title = row["title"]
        out[ticker] = {"cik": cik, "title": title}
    return out


def sec_primary_doc_url(cik: str, accession_number: str, primary_document: str) -> str:
    accession_nodashes = accession_number.replace("-", "")
    cik_noleading = str(int(cik))
    return f"{SEC_ARCHIVES_BASE}/{cik_noleading}/{accession_nodashes}/{primary_document}"


def sec_complete_submission_url(cik: str, accession_number: str) -> str:
    accession_nodashes = accession_number.replace("-", "")
    cik_noleading = str(int(cik))
    return f"{SEC_ARCHIVES_BASE}/{cik_noleading}/{accession_nodashes}/{accession_number}.txt"


def normalize_recent_filings(submissions: Dict[str, Any]) -> List[Dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent", {})
    keys = list(recent.keys())
    rows = []
    if not keys:
        return rows
    n = len(recent[keys[0]])
    for i in range(n):
        row = {k: recent[k][i] for k in keys}
        rows.append(row)
    return rows


def iter_all_submission_files(client: SimpleHTTPClient, submissions: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for row in normalize_recent_filings(submissions):
        yield row

    old_files = submissions.get("filings", {}).get("files", []) or []
    for f in old_files:
        name = f.get("name")
        if not name:
            continue
        historical_url = f"https://data.sec.gov/submissions/{name}"
        historical = client.get_json(historical_url)
        for row in normalize_recent_filings(historical):
            yield row


def find_latest_forms(
    client: SimpleHTTPClient,
    cik: str,
    desired_forms: set[str],
    limit_per_form: int = 2,
) -> Dict[str, List[Dict[str, Any]]]:
    submissions = client.get_json(SEC_SUBMISSIONS_URL.format(cik=cik))
    collected: Dict[str, List[Dict[str, Any]]] = {form: [] for form in desired_forms}

    for row in iter_all_submission_files(client, submissions):
        form = str(row.get("form", "")).upper()
        if form in desired_forms and len(collected[form]) < limit_per_form:
            collected[form].append(row)
        if all(len(v) >= limit_per_form for v in collected.values()):
            break

    return collected


def strip_html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "ix:header", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def save_text(path: Path, content: str) -> None:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")


def save_json(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch_and_store_10k(
    client: SimpleHTTPClient,
    output_dir: Path,
    ticker: str,
    company_name: str,
    cik: str,
    filing_row: Dict[str, Any],
) -> List[ManifestRecord]:
    records: List[ManifestRecord] = []

    accession = filing_row.get("accessionNumber")
    filing_date = filing_row.get("filingDate")
    report_date = filing_row.get("reportDate")
    form = filing_row.get("form")
    primary_document = filing_row.get("primaryDocument")

    if not accession or not primary_document:
        return records

    company_dir = output_dir / ticker / "10K"
    ensure_dir(company_dir)

    base_name = f"{filing_date}_{form}_{accession.replace('-', '')}"
    raw_html_path = company_dir / f"{base_name}.html"
    raw_submission_path = company_dir / f"{base_name}_submission.txt"
    text_path = company_dir / f"{base_name}.txt"
    meta_path = company_dir / f"{base_name}.json"

    html_url = sec_primary_doc_url(cik, accession, primary_document)
    submission_url = sec_complete_submission_url(cik, accession)

    try:
        html_text = client.get_text(html_url)
        save_text(raw_html_path, html_text)
        save_text(text_path, strip_html_to_text(html_text))
    except Exception as exc:
        print(f"[WARN] Failed primary document for {ticker} {accession}: {exc}", file=sys.stderr)

    try:
        submission_text = client.get_text(submission_url)
        save_text(raw_submission_path, submission_text)
    except Exception as exc:
        print(f"[WARN] Failed complete submission for {ticker} {accession}: {exc}", file=sys.stderr)

    metadata = {
        "ticker": ticker,
        "company_name": company_name,
        "cik": cik,
        "accession_number": accession,
        "filing_date": filing_date,
        "period_of_report": report_date,
        "form": form,
        "primary_document": primary_document,
        "sec_primary_document_url": html_url,
        "sec_complete_submission_url": submission_url,
    }
    save_json(meta_path, metadata)

    if raw_html_path.exists():
        records.append(
            ManifestRecord(
                ticker=ticker,
                company_name=company_name,
                cik=cik,
                doc_type="10K_HTML",
                source="SEC",
                filing_date=filing_date,
                period_of_report=report_date,
                accession_number=accession,
                form=form,
                local_path=str(raw_html_path),
                metadata={"url": html_url, "primary_document": primary_document},
            )
        )
    if text_path.exists():
        records.append(
            ManifestRecord(
                ticker=ticker,
                company_name=company_name,
                cik=cik,
                doc_type="10K_TEXT",
                source="SEC",
                filing_date=filing_date,
                period_of_report=report_date,
                accession_number=accession,
                form=form,
                local_path=str(text_path),
                metadata={"derived_from": str(raw_html_path) if raw_html_path.exists() else None},
            )
        )
    if raw_submission_path.exists():
        records.append(
            ManifestRecord(
                ticker=ticker,
                company_name=company_name,
                cik=cik,
                doc_type="10K_SUBMISSION",
                source="SEC",
                filing_date=filing_date,
                period_of_report=report_date,
                accession_number=accession,
                form=form,
                local_path=str(raw_submission_path),
                metadata={"url": submission_url},
            )
        )
    records.append(
        ManifestRecord(
            ticker=ticker,
            company_name=company_name,
            cik=cik,
            doc_type="10K_METADATA",
            source="SEC",
            filing_date=filing_date,
            period_of_report=report_date,
            accession_number=accession,
            form=form,
            local_path=str(meta_path),
            metadata={},
        )
    )
    return records


def fetch_fmp_transcript_dates(
    client: SimpleHTTPClient,
    ticker: str,
    api_key: str,
    limit: int = 4,
) -> List[Dict[str, Any]]:
    params = {"symbol": ticker, "apikey": api_key}
    data = client.get_json(FMP_TRANSCRIPT_DATES_URL, params=params)
    if not isinstance(data, list):
        return []
    # Sort descending by date if present
    def key_fn(x: Dict[str, Any]) -> str:
        return str(x.get("date", ""))

    data = sorted(data, key=key_fn, reverse=True)
    return data[:limit]


def fetch_and_store_transcript(
    client: SimpleHTTPClient,
    output_dir: Path,
    ticker: str,
    company_name: str,
    cik: str,
    api_key: str,
    transcript_date_row: Dict[str, Any],
) -> Optional[ManifestRecord]:
    date_str = transcript_date_row.get("date")
    quarter = transcript_date_row.get("quarter")
    year = transcript_date_row.get("year")

    if year is None or quarter is None:
        return None

    params = {"symbol": ticker, "year": year, "quarter": quarter, "apikey": api_key}
    data = client.get_json(FMP_TRANSCRIPT_URL, params=params)

    # FMP often returns a list with one record.
    if isinstance(data, list) and data:
        payload = data[0]
    elif isinstance(data, dict):
        payload = data
    else:
        return None

    company_dir = output_dir / ticker / "transcripts"
    ensure_dir(company_dir)

    fname = f"{date_str or year}_Q{quarter}_transcript.json"
    out_path = company_dir / fname
    save_json(out_path, payload)

    return ManifestRecord(
        ticker=ticker,
        company_name=company_name,
        cik=cik,
        doc_type="EARNINGS_TRANSCRIPT",
        source="FinancialModelingPrep",
        filing_date=str(date_str) if date_str else None,
        period_of_report=None,
        accession_number=None,
        form=None,
        local_path=str(out_path),
        metadata={"year": year, "quarter": quarter},
    )


def create_analyst_report_placeholder(output_dir: Path, ticker: str, company_name: str) -> None:
    analyst_dir = output_dir / ticker / "analyst_reports"
    ensure_dir(analyst_dir)

    readme = analyst_dir / "README.txt"
    if not readme.exists():
        readme.write_text(
            "\n".join(
                [
                    f"{company_name} ({ticker}) analyst reports",
                    "",
                    "True sell-side analyst reports are usually licensed and not freely bulk-downloadable.",
                    "Drop any legally obtained PDFs or text files into this folder.",
                    "",
                    "Suggested filenames:",
                    "  2026-01-15_gs_initiation.pdf",
                    "  2026-02-03_ms_downgrade.txt",
                    "",
                    "You can also add a urls.txt file with one source URL per line for public commentary",
                    "that you want your pipeline to ingest separately.",
                ]
            ),
            encoding="utf-8",
        )


def write_company_summary(
    output_dir: Path,
    ticker: str,
    company_name: str,
    cik: str,
    records: List[ManifestRecord],
) -> None:
    company_dir = output_dir / ticker
    summary = {
        "ticker": ticker,
        "company_name": company_name,
        "cik": cik,
        "record_count": len(records),
        "records_by_type": {},
    }
    counts: Dict[str, int] = {}
    for r in records:
        counts[r.doc_type] = counts.get(r.doc_type, 0) + 1
    summary["records_by_type"] = counts

    save_json(company_dir / "company_summary.json", summary)


def append_manifest(manifest_path: Path, records: List[ManifestRecord]) -> None:
    ensure_dir(manifest_path.parent)
    with manifest_path.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build starter financial dataset.")
    parser.add_argument("--output", default="data/financial_dataset", help="Output directory")
    parser.add_argument("--tickers", nargs="*", default=DEFAULT_TICKERS, help="Ticker list")
    parser.add_argument("--years-10k", type=int, default=2, help="How many 10-Ks per company")
    parser.add_argument("--transcripts", type=int, default=4, help="How many transcript dates to fetch")
    args = parser.parse_args()

    sec_user_agent = read_env("SEC_USER_AGENT")
    if not sec_user_agent:
        print(
            "ERROR: Set SEC_USER_AGENT first, e.g.\n"
            '  export SEC_USER_AGENT="Your Name your_email@example.com"\n',
            file=sys.stderr,
        )
        return 1

    fmp_api_key = read_env("FMP_API_KEY")
    output_dir = Path(args.output)
    ensure_dir(output_dir)

    client = SimpleHTTPClient(user_agent=sec_user_agent, min_interval_seconds=0.25)

    print("[INFO] Loading SEC ticker map...")
    ticker_map = load_sec_ticker_map(client)

    manifest_path = output_dir / "manifest.jsonl"
    if manifest_path.exists():
        manifest_path.unlink()

    dataset_meta = {
        "generated_at_epoch": int(time.time()),
        "tickers_requested": args.tickers,
        "years_10k": args.years_10k,
        "transcripts_per_company": args.transcripts,
        "sec_user_agent_present": True,
        "fmp_transcripts_enabled": bool(fmp_api_key),
    }
    save_json(output_dir / "dataset_metadata.json", dataset_meta)

    for ticker in args.tickers:
        ticker = ticker.upper().strip()
        print(f"\n[INFO] Processing {ticker}...")

        if ticker not in ticker_map:
            print(f"[WARN] Ticker not found in SEC mapping: {ticker}", file=sys.stderr)
            continue

        company_name = ticker_map[ticker]["title"]
        cik = ticker_map[ticker]["cik"]

        company_root = output_dir / ticker
        ensure_dir(company_root / "10K")
        ensure_dir(company_root / "transcripts")
        ensure_dir(company_root / "presentations")
        create_analyst_report_placeholder(output_dir, ticker, company_name)

        all_records: List[ManifestRecord] = []

        # 10-Ks
        try:
            filings = find_latest_forms(client, cik=cik, desired_forms={"10-K"}, limit_per_form=args.years_10k)
            ten_ks = filings.get("10-K", [])
            if not ten_ks:
                print(f"[WARN] No 10-Ks found for {ticker}", file=sys.stderr)
            for row in ten_ks:
                recs = fetch_and_store_10k(client, output_dir, ticker, company_name, cik, row)
                all_records.extend(recs)
        except Exception as exc:
            print(f"[WARN] Failed 10-K retrieval for {ticker}: {exc}", file=sys.stderr)

        # Transcripts
        if fmp_api_key:
            try:
                transcript_dates = fetch_fmp_transcript_dates(client, ticker, fmp_api_key, limit=args.transcripts)
                for td in transcript_dates:
                    rec = fetch_and_store_transcript(client, output_dir, ticker, company_name, cik, fmp_api_key, td)
                    if rec:
                        all_records.append(rec)
            except Exception as exc:
                print(f"[WARN] Failed transcript retrieval for {ticker}: {exc}", file=sys.stderr)
        else:
            note = (
                company_root / "transcripts" / "README.txt"
            )
            note.write_text(
                "\n".join(
                    [
                        f"{company_name} ({ticker}) transcripts",
                        "",
                        "Set FMP_API_KEY to auto-download transcripts from Financial Modeling Prep.",
                        "Without that key, you can manually place transcript JSON/TXT files here.",
                    ]
                ),
                encoding="utf-8",
            )

        write_company_summary(output_dir, ticker, company_name, cik, all_records)
        append_manifest(manifest_path, all_records)
        print(f"[INFO] {ticker}: wrote {len(all_records)} records")

    print(f"\n[DONE] Dataset created at: {output_dir.resolve()}")
    print(f"[DONE] Manifest: {manifest_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
