"""
tools/web_scraper.py — URL-based debate text extraction.

Supports:
  - Reddit threads (via Reddit's public JSON API — no credentials required)
  - Generic web pages / forums (via requests + BeautifulSoup)

Usage:
    scraper = WebScraper()
    text = scraper.scrape("https://www.reddit.com/r/changemyview/comments/xyz/...")
"""

import re
import textwrap
import requests
from bs4 import BeautifulSoup

# Matches any Reddit post URL (old.reddit.com and www.reddit.com)
_REDDIT_RE = re.compile(r"reddit\.com/r/\w+/comments/\w+", re.IGNORECASE)

# Max characters per comment to keep token usage manageable
_MAX_COMMENT_CHARS = 800

# Cap on total comments extracted
_MAX_COMMENTS = 60


class WebScraper:
    """
    Fetches and formats debate content from a URL into plain text that
    the ClaimExtractor can process.
    """

    HEADERS = {
        "User-Agent": "DebateJudge/1.0 (academic debate analysis tool)"
    }

    def scrape(self, url: str) -> str:
        """
        Auto-detect the site type and return formatted debate text.
        Raises requests.HTTPError / ValueError on failure.
        """
        if _REDDIT_RE.search(url):
            return self._scrape_reddit(url)
        return self._scrape_generic(url)

    # ── Reddit ────────────────────────────────────────────────────────────────

    def _scrape_reddit(self, url: str) -> str:
        """
        Uses Reddit's public JSON API (no OAuth required for public posts).
        Formats the thread as:

            [Title]: <post title>
            u/<author>: <post body>
            u/<commenter>: <comment text>
              u/<replier>: <reply text>   (indented to show thread depth)
        """
        # Strip query params and ensure we hit the JSON endpoint
        base_url = url.split("?")[0].rstrip("/")
        json_url = f"{base_url}.json?limit=100&depth=4"

        resp = requests.get(json_url, headers=self.HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if not isinstance(data, list) or len(data) < 2:
            raise ValueError("Unexpected Reddit API response format.")

        post_data = data[0]["data"]["children"][0]["data"]
        title = post_data.get("title", "(no title)")
        selftext = (post_data.get("selftext") or "").strip()
        op_author = post_data.get("author", "OP")

        lines = [f"[Post Title]: {title}"]
        if selftext and selftext not in ("[deleted]", "[removed]"):
            lines.append(f"u/{op_author}: {selftext[:_MAX_COMMENT_CHARS]}")

        comment_count = [0]  # mutable counter for the recursive helper
        self._walk_reddit_comments(
            data[1]["data"]["children"],
            lines,
            depth=0,
            max_depth=4,
            counter=comment_count
        )

        if not lines:
            raise ValueError("No readable content found in this Reddit thread.")

        return "\n\n".join(lines)

    def _walk_reddit_comments(self, children, lines, depth, max_depth, counter):
        """Recursively walk Reddit comment tree, respecting depth + count caps."""
        for child in children:
            if counter[0] >= _MAX_COMMENTS:
                break
            if child.get("kind") != "t1":
                continue

            comment = child["data"]
            author = comment.get("author", "[deleted]")
            body = (comment.get("body") or "").strip()

            if body and body not in ("[deleted]", "[removed]"):
                prefix = "  " * depth
                truncated = textwrap.shorten(body, width=_MAX_COMMENT_CHARS,
                                             placeholder="…")
                lines.append(f"{prefix}u/{author}: {truncated}")
                counter[0] += 1

            if depth < max_depth:
                replies = comment.get("replies", "")
                if isinstance(replies, dict):
                    reply_children = replies["data"]["children"]
                    self._walk_reddit_comments(
                        reply_children, lines, depth + 1, max_depth, counter
                    )

    # ── Generic / Other Forums ────────────────────────────────────────────────

    def _scrape_generic(self, url: str) -> str:
        """
        Fetches a generic web page and extracts readable text using
        BeautifulSoup. Works for most text-heavy discussion forums.
        """
        resp = requests.get(url, headers=self.HEADERS, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise
        for tag in soup(["script", "style", "nav", "footer",
                          "header", "aside", "form", "noscript"]):
            tag.decompose()

        # Prefer article / main content if available, fall back to body
        main = (
            soup.find("article")
            or soup.find("main")
            or soup.find(id=re.compile(r"content|main|post", re.I))
            or soup.find("body")
        )

        raw = (main or soup).get_text(separator="\n", strip=True)

        # Collapse excessive blank lines
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        text = "\n".join(lines)

        if not text:
            raise ValueError("Could not extract any readable text from the page.")

        return text
