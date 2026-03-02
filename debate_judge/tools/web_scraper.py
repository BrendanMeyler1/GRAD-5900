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
_MAX_COMMENT_CHARS = 1000

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
            # Allow much more context for the Origin Post (4000 chars)
            lines.append(f"[Original Post Context]\nu/{op_author}: {selftext[:4000]}\n")

        # 1. Score all top-level comment threads by depth/descendants
        top_level_comments = data[1]["data"]["children"]
        scored_threads = []
        
        for idx, child in enumerate(top_level_comments):
            if child.get("kind") != "t1":
                continue
            depth, descendants = self._score_comment_tree(child, 0)
            # A good debate is deep, but also has multiple replies (descendants)
            score = (depth * 2) + descendants
            scored_threads.append((score, depth, descendants, child))
            
        # 2. Sort threads by score descending to find the "Most Notable Debates"
        scored_threads.sort(key=lambda x: x[0], reverse=True)
        
        # 3. Walk the best threads until we hit our comment cap
        comment_count = [0]
        for score, depth, desc, child in scored_threads:
            if comment_count[0] >= _MAX_COMMENTS:
                break
            
            lines.append(f"\n--- [Notable Debate Thread: Depth {depth}, Replies {desc}] ---")
            
            # Walk this specific high-value thread
            self._walk_reddit_comments(
                [child],
                lines,
                depth=0,
                max_depth=6, # Allow deeper trees for notable debates
                counter=comment_count
            )

        if len(lines) <= 1: # Only title was added
            raise ValueError("No readable content found in this Reddit thread.")

        return "\n".join(lines)

    def _score_comment_tree(self, child: dict, current_depth: int) -> tuple[int, int]:
        """
        Recursively scores a comment tree to find the most active debates.
        Returns (max_depth, total_descendants).
        """
        if child.get("kind") != "t1":
            return current_depth, 0
            
        comment = child["data"]
        replies = comment.get("replies", "")
        
        if not isinstance(replies, dict):
            return current_depth, 0
            
        reply_children = replies["data"]["children"]
        max_child_depth = current_depth
        total_descendants = len(reply_children)
        
        for reply in reply_children:
            child_depth, child_desc = self._score_comment_tree(reply, current_depth + 1)
            max_child_depth = max(max_child_depth, child_depth)
            total_descendants += child_desc
            
        return max_child_depth, total_descendants

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
