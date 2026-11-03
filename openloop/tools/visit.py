"""Web visit tool — fetch a page via Jina AI, then summarize with an LLM."""

import json
import os
import time

import requests

from .base import Tool

JINA_API_KEYS = os.getenv("JINA_API_KEYS", "")
VISIT_SERVER_TIMEOUT = int(os.getenv("VISIT_SERVER_TIMEOUT", "200"))
WEBCONTENT_MAXLENGTH = int(os.getenv("WEBCONTENT_MAXLENGTH", "150000"))

SUMMARY_API_URL = os.getenv("SUMMARY_API_URL", "")
SUMMARY_API_KEY = os.getenv("SUMMARY_API_KEY", "")
SUMMARY_MODEL_NAME = os.getenv("SUMMARY_MODEL_NAME", "")

EXTRACTOR_PROMPT = """Please process the following webpage content and user goal to extract relevant information:

## **Webpage Content**
{webpage_content}

## **User Goal**
{goal}

## **Task Guidelines**
1. **Content Scanning for Rational**: Locate the **specific sections/data** directly related to the user's goal within the webpage content
2. **Key Extraction for Evidence**: Identify and extract the **most relevant information** from the content, you never miss any important information, output the **full original context** of the content as far as possible, it can be more than three paragraphs.
3. **Summary Output for Summary**: Organize into a concise paragraph with logical flow, prioritizing clarity and judge the contribution of the information to the goal.

**Final Output Format using JSON format has "rational", "evidence", "summary" fields**
"""


def _truncate_to_tokens(text: str, max_tokens: int = 95000) -> str:
    """Truncate text to max_tokens using tiktoken. Falls back to character estimation."""
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        tokens = encoding.encode(text, allowed_special="all")
        if len(tokens) <= max_tokens:
            return text
        return encoding.decode(tokens[:max_tokens])
    except ImportError:
        # Rough fallback: ~3.5 chars per token on average
        max_chars = int(max_tokens * 3.5)
        if len(text) <= max_chars:
            return text
        return text[:max_chars]


class VisitTool(Tool):
    """Visit a webpage and extract relevant information using Jina AI + LLM summarization."""

    name = "web_visit"
    description = (
        "Visit a webpage and extract structured information relevant to a goal. "
        "Use after web_search returns promising URLs. Provide a clear 'goal' describing what to extract."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to visit",
            },
            "goal": {
                "type": "string",
                "description": "What information to extract from the page",
            },
        },
        "required": ["url", "goal"],
    }

    def __init__(self, summary_client=None, summary_model: str = ""):
        """
        Args:
            summary_client: Optional OpenAI-compatible client for summarization.
                           If None, creates one from SUMMARY_API_URL/KEY/MODEL env vars.
            summary_model: Model name override. If empty, uses SUMMARY_MODEL_NAME env var.
        """
        self._summary_client = summary_client
        self._summary_model = summary_model or SUMMARY_MODEL_NAME

    def execute(self, url: str, goal: str) -> str:
        return self._readpage_jina(url, goal)

    def _fetch_page(self, url: str, max_retries: int = 3) -> str:
        """Fetch webpage content via Jina AI reader."""
        api_key = JINA_API_KEYS
        if not api_key:
            return "[visit] Error: JINA_API_KEYS environment variable not set. Get a key at https://jina.ai"

        timeout = VISIT_SERVER_TIMEOUT
        headers = {"Authorization": f"Bearer {api_key}"}

        for attempt in range(max_retries):
            try:
                response = requests.get(
                    f"https://r.jina.ai/{url}",
                    headers=headers,
                    timeout=timeout,
                )
                if response.status_code == 200:
                    content = response.text
                    if content and not content.startswith("[visit] Failed") and not content.startswith("[document_parser]"):
                        return content
            except Exception:
                pass
            if attempt < max_retries - 1:
                time.sleep(0.5)
        return "[visit] Failed to read page."

    def _summarize(self, content: str, goal: str, max_retries: int = 2) -> str:
        """Call summarization LLM and return raw JSON string."""
        client = self._summary_client
        model = self._summary_model

        if client is None:
            api_url = SUMMARY_API_URL
            api_key = SUMMARY_API_KEY
            if not api_url or not api_key:
                return ""
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url=api_url)
                self._summary_client = client
            except ImportError:
                return ""

        if not model:
            return ""

        messages = [{"role": "user", "content": EXTRACTOR_PROMPT.format(webpage_content=content, goal=goal)}]

        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.7,
                )
                raw = response.choices[0].message.content
                if raw:
                    try:
                        json.loads(raw)
                    except Exception:
                        left = raw.find("{")
                        right = raw.rfind("}")
                        if left != -1 and right != -1 and left <= right:
                            raw = raw[left:right + 1]
                    return raw
            except Exception as e:
                print(f"[visit] Summarization attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    return ""
        return ""

    def _readpage_jina(self, url: str, goal: str) -> str:
        """Full pipeline: fetch → truncate → summarize → format."""
        content = self._fetch_page(url, max_retries=8)

        if content.startswith("[visit] Failed") or content.startswith("[visit] Error"):
            # Preserve the original error message for API key issues
            if "environment variable not set" in content:
                return content
            return f"[visit] Failed to visit the url: {url}"

        content = _truncate_to_tokens(content, max_tokens=95000)

        # First summarization attempt
        raw = self._summarize(content, goal)

        # Retry with progressively truncated content if summary is too short
        summary_retries = 3
        while len(raw) < 10 and summary_retries > 0:
            truncate_length = int(0.7 * len(content)) if summary_retries > 1 else 25000
            content = content[:truncate_length]
            raw = self._summarize(content, goal)
            summary_retries -= 1

        # Parse JSON result with retries
        if isinstance(raw, str):
            raw = raw.replace("```json", "").replace("```", "").strip()

        parse_retries = 0
        parsed = None
        while parse_retries < 3:
            try:
                parsed = json.loads(raw)
                break
            except Exception:
                raw = self._summarize(content, goal)
                if raw:
                    raw = raw.replace("```json", "").replace("```", "").strip()
                parse_retries += 1

        if parsed is None:
            return (
                f"The useful information in {url} for user goal {goal} as follows:\n\n"
                "Evidence in page:\n"
                "The provided webpage content could not be parsed. Please check the URL or file format.\n\n"
                "Summary:\n"
                "The webpage content could not be processed, and therefore, no information is available.\n"
            )

        result = f"The useful information in {url} for user goal {goal} as follows:\n\n"
        result += f"Evidence in page:\n{parsed.get('evidence', '')}\n\n"
        result += f"Summary:\n{parsed.get('summary', '')}\n"

        return result
