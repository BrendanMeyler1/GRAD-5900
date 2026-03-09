"""
tools/duckduckgo_tool.py — Web search fallback using DuckDuckGo.

Requires: pip install duckduckgo-search

Used as a secondary evidence source when Wikipedia returns no relevant
result. Fetches the top web snippets for a query and returns them as a
combined text block the LLM can reason over.

No API key required.
"""

from duckduckgo_search import DDGS


class DuckDuckGoTool:
    """
    Searches the web via DuckDuckGo and returns a plain-text evidence block
    built from the top result snippets.
    """

    def __init__(self, max_results: int = 3):
        self.max_results = max_results

    def search_summary(self, query: str) -> str | None:
        """
        Run a DuckDuckGo text search and return a formatted evidence string,
        or None if no results are found.

        Each result is formatted as:
            Source: <title> (<url>)
            Content: <snippet>
        """
        try:
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=self.max_results):
                    title   = r.get("title", "Unknown")
                    url     = r.get("href", "")
                    snippet = r.get("body", "").strip()
                    if snippet:
                        results.append(
                            f"Source: {title} ({url})\nContent: {snippet}"
                        )

            if not results:
                return None

            return "\n\n---\n\n".join(results)

        except Exception as e:
            print(f"  [DuckDuckGo] Search error: {e}")
            return None
