"""Web search tool — Serper.dev (Google) or Tavily."""

import json
import os
import time

import requests

from .base import Tool

SERPER_BASE_URL = (os.environ.get("SERPER_BASE_URL") or "https://google.serper.dev").rstrip("/")


def _get_serper_key() -> str:
    return (
        os.environ.get("SERPER_API_KEY")
        or os.environ.get("SERPER_KEY_ID")
        or ""
    )


def _get_tavily_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "")


def _get_search_provider() -> str:
    return os.environ.get("SEARCH_PROVIDER", "serper").strip().lower()


def _contains_chinese(text: str) -> bool:
    return any('一' <= ch <= '鿿' for ch in text)


class SearchTool(Tool):
    """Search the web using Serper.dev (Google) or Tavily."""

    name = "web_search"
    description = (
        "Search the web. Returns top 10 results with title, URL, date, and snippet. "
        "Use this to find current information, documentation, or facts."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query string",
            },
        },
        "required": ["query"],
    }

    def __init__(self, provider: str | None = None, filter_huggingface: bool = False):
        self.provider = provider or _get_search_provider()
        self.filter_huggingface = filter_huggingface

    def execute(self, query: str) -> str:
        if self.provider == "tavily":
            return self._search_tavily(query)
        return self._search_serper(query)

    def _search_serper(self, query: str) -> str:
        api_key = _get_serper_key()
        if not api_key:
            return "Error: SERPER_API_KEY or SERPER_KEY_ID environment variable not set. Get a key at https://serper.dev"

        if _contains_chinese(query):
            payload = {"q": query, "location": "China", "gl": "cn", "hl": "zh-cn"}
        else:
            payload = {"q": query, "location": "United States", "gl": "us", "hl": "en"}

        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        }

        last_error = None
        for attempt in range(5):
            try:
                response = requests.post(
                    f"{SERPER_BASE_URL}/search",
                    data=json.dumps(payload),
                    headers=headers,
                    timeout=30,
                )
                break
            except Exception as e:
                last_error = e
                if attempt < 4:
                    time.sleep(2 ** attempt)
                continue
        else:
            return f"Error: Google search failed after 5 attempts: {last_error}"

        if response.status_code >= 400:
            try:
                results = response.json()
            except Exception:
                results = {}
            message = results.get("message") or results.get("error") or response.text
            return f"Error: Serper API returned status {response.status_code}: {message}"

        try:
            results = response.json()
        except Exception:
            return f"Error: Failed to parse Serper response as JSON: {response.text[:500]}"

        if "organic" not in results:
            return f"No results found for '{query}'. Try a more general query."

        web_snippets = []
        idx = 0
        for page in results["organic"]:
            link = str(page.get("link", ""))
            if self.filter_huggingface and "huggingface" in link.lower():
                continue
            idx += 1

            date_published = f"\nDate published: {page['date']}" if "date" in page else ""
            source = f"\nSource: {page['source']}" if "source" in page else ""
            snippet = f"\n{page['snippet']}" if "snippet" in page else ""

            entry = f"{idx}. [{page['title']}]({page['link']}){date_published}{source}\n{snippet}"
            entry = entry.replace("Your browser can't play this video.", "")
            web_snippets.append(entry)

        return f"### A Google search for '{query}' found {len(web_snippets)} results:\n\n" + "\n\n".join(web_snippets)

    def _search_tavily(self, query: str) -> str:
        api_key = _get_tavily_key()
        if not api_key:
            return "Error: TAVILY_API_KEY environment variable not set. Get a key at https://tavily.com"

        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=api_key)
            response = client.search(query=query, max_results=10)
        except ImportError:
            return "Error: tavily-python package not installed. Install with: pip install tavily-python"
        except Exception as e:
            return f"Error: Tavily search failed: {e}"

        results = response.get("results", [])
        if not results:
            return f"No results found for '{query}'. Try a more general query."

        web_snippets = []
        for idx, result in enumerate(results, start=1):
            url = str(result.get("url", ""))
            if self.filter_huggingface and "huggingface" in url.lower():
                continue

            title = result.get("title", "")
            content = result.get("content", "")
            date_published = ""
            if result.get("published_date"):
                date_published = f"\nDate published: {result['published_date']}"

            entry = f"{len(web_snippets) + 1}. [{title}]({url}){date_published}\n{content}"
            web_snippets.append(entry)

        if not web_snippets:
            return f"No results found for '{query}'. Try a more general query."

        return f"### A Tavily search for '{query}' found {len(web_snippets)} results:\n\n" + "\n\n".join(web_snippets)
