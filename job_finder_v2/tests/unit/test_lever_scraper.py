"""Unit tests for scrapers.lever.LeverScraper — HTML parsing with fixture HTML."""

from __future__ import annotations

import pytest

from scrapers.lever import LeverScraper


_LEVER_HTML = """
<html>
<head><title>Ramp — Jobs</title></head>
<body>
  <div class="posting">
    <a class="posting-title" href="https://jobs.lever.co/ramp/backend-eng-123">
      <h5>Backend Engineer</h5>
    </a>
    <div class="posting-categories">
      <span class="location">New York, NY</span>
      <span class="commitment">Full-time</span>
    </div>
    <div class="posting-description">Build financial infrastructure in Python.</div>
  </div>

  <div class="posting">
    <a class="posting-title" href="https://jobs.lever.co/ramp/design-456">
      <h5>Product Designer</h5>
    </a>
    <div class="posting-categories">
      <span class="location">Remote</span>
    </div>
  </div>

  <div class="posting">
    <!-- missing link -> should be skipped -->
    <h5>Ghost Posting</h5>
  </div>
</body>
</html>
"""


@pytest.fixture
def scraper() -> LeverScraper:
    return LeverScraper(companies=["ramp"])


def test_parse_html_extracts_postings(scraper: LeverScraper) -> None:
    results = scraper._parse_html(_LEVER_HTML, "ramp")
    assert len(results) == 2  # third posting is malformed
    titles = [j.title for j in results]
    assert "Backend Engineer" in titles
    assert "Product Designer" in titles


def test_display_name_from_page_title(scraper: LeverScraper) -> None:
    results = scraper._parse_html(_LEVER_HTML, "ramp")
    assert all(j.company == "Ramp" for j in results)


def test_ats_type_is_lever(scraper: LeverScraper) -> None:
    results = scraper._parse_html(_LEVER_HTML, "ramp")
    for job in results:
        assert job.ats_type == "lever"
        assert job.source == "lever"


def test_remote_inferred_from_location(scraper: LeverScraper) -> None:
    results = scraper._parse_html(_LEVER_HTML, "ramp")
    designer = next(j for j in results if j.title == "Product Designer")
    # "Remote" in location triggers remote_ok via JobListing.__post_init__
    assert designer.remote_ok is True


def test_apply_url_preserved(scraper: LeverScraper) -> None:
    results = scraper._parse_html(_LEVER_HTML, "ramp")
    backend = next(j for j in results if j.title == "Backend Engineer")
    assert backend.apply_url == "https://jobs.lever.co/ramp/backend-eng-123"


def test_malformed_posting_skipped(scraper: LeverScraper) -> None:
    results = scraper._parse_html(_LEVER_HTML, "ramp")
    titles = [j.title for j in results]
    assert "Ghost Posting" not in titles
