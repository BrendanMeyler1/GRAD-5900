import warnings
import wikipedia

# Suppress the BeautifulSoup parser warning emitted by the wikipedia library
# internally — we have no way to pass 'features' to its BeautifulSoup call.
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    message=".*No parser was explicitly specified.*"
)

class WikipediaTool:
    def __init__(self):
        # Set a user agent to avoid Wikipedia API blocking
        wikipedia.set_user_agent("DebateJudge/1.0 (https://github.com/yourname/debate-judge)")

    def search_summary(self, query):
        """
        Searches Wikipedia for the query and returns the summary of the top results.
        Tries the top 2 search results and concatenates their summaries for a richer
        evidence window. Returns None if no result is found or on error.
        """
        try:
            results = wikipedia.search(query, results=2)
            if not results:
                return None

            evidence_parts = []
            for page_title in results:
                try:
                    summary = wikipedia.summary(page_title, sentences=10, auto_suggest=False)
                    evidence_parts.append(f"Source: Wikipedia ({page_title})\nContent: {summary}")
                except wikipedia.exceptions.DisambiguationError as e:
                    try:
                        summary = wikipedia.summary(e.options[0], sentences=10, auto_suggest=False)
                        evidence_parts.append(f"Source: Wikipedia ({e.options[0]})\nContent: {summary}")
                    except Exception:
                        continue
                except wikipedia.exceptions.PageError:
                    continue
                except Exception:
                    continue

            if not evidence_parts:
                return None

            return "\n\n---\n\n".join(evidence_parts)

        except Exception as e:
            print(f"Error searching Wikipedia: {e}")
            return None

if __name__ == "__main__":
    tool = WikipediaTool()
    print(tool.search_summary("nuclear power deaths per TWh safety"))
