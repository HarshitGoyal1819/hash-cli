"""Web search and fetch tools for the hash-cli agent."""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def web_search(query: str, max_results: int = 8) -> str:
    """Search the web using DuckDuckGo and return a summary of results.

    Use this to look up documentation, find solutions to errors, research
    third-party tools (Celonis, Salesforce, AWS, etc.), check latest versions,
    or get any current information.

    Args:
        query:       The search query string.
        max_results: Number of results to return. Default 8, max 15.

    Returns:
        A formatted list of results with title, URL, and snippet.
    """
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # fallback for older installs

        max_results = min(max(max_results, 1), 15)
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(r)

        if not results:
            return f"No results found for: {query}"

        lines = [f"Web search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            url = r.get("href", "")
            snippet = r.get("body", "")[:400]
            lines.append(f"{i}. **{title}**\n   URL: {url}\n   {snippet}\n")

        return "\n".join(lines)

    except ImportError:
        return "Error: ddgs not installed. Run: pip install ddgs"
    except Exception as exc:
        return f"Error performing web search: {exc}"


@tool
def web_fetch(url: str, max_chars: int = 8000) -> str:
    """Fetch and extract readable text content from a URL.

    Use this after web_search to read full documentation pages, articles,
    GitHub READMEs, API references, or any web page. Ideal for deep research
    on tools like Celonis, Salesforce, or any technical docs.

    Args:
        url:       Full URL to fetch (https://...).
        max_chars: Maximum characters of content to return. Default 8000.

    Returns:
        Cleaned readable text extracted from the page, or an error message.
    """
    try:
        import httpx
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        with httpx.Client(follow_redirects=True, timeout=15) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")

        # Plain text / JSON — return directly
        if "text/plain" in content_type or "application/json" in content_type:
            return resp.text[:max_chars]

        # HTML — parse and extract meaningful text
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "footer", "header",
                          "aside", "form", "noscript", "iframe"]):
            tag.decompose()

        # Try to find the main content area first
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find(id="content")
            or soup.find(class_="content")
            or soup.find(class_="documentation")
            or soup.find(class_="markdown-body")  # GitHub
            or soup.body
        )

        if main:
            text = main.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        # Collapse excessive blank lines
        import re
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        if not text:
            return f"No readable content found at {url}"

        truncated = text[:max_chars]
        suffix = f"\n\n[Truncated — showing {max_chars} of {len(text)} chars]" if len(text) > max_chars else ""
        return f"Content from: {url}\n\n{truncated}{suffix}"

    except ImportError as e:
        return f"Error: Missing dependency — {e}. Run: pip install httpx beautifulsoup4"
    except Exception as exc:
        return f"Error fetching {url}: {exc}"
