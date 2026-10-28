"""Fetch the plain text of a Wikipedia article."""

import re
import urllib.parse

from reconciliation.enrichment.wikimedia import NotFound, get_json

ARTICLE_URL_RE = re.compile(
    r"^https?://([a-z0-9-]+)\.(?:m\.)?wikipedia\.org/wiki/([^#?]+)", re.IGNORECASE
)


def parse_url(url: str) -> tuple[str, str]:
    """Split an article URL into (language code, page title)."""
    match = ARTICLE_URL_RE.match(url.strip())
    if not match:
        raise ValueError(f"Not a Wikipedia article URL: {url!r}")
    lang = match.group(1).lower()
    title = urllib.parse.unquote(match.group(2)).replace("_", " ").strip()
    if not title:
        raise ValueError(f"Wikipedia URL has no article title: {url!r}")
    return lang, title


def fetch_extract(lang: str, title: str) -> tuple[str, str]:
    """Return (canonical title, full article as plain text), following redirects."""
    data = get_json(
        f"https://{lang}.wikipedia.org/w/api.php",
        {
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "redirects": 1,
            "formatversion": 2,
            "titles": title,
        },
    )
    pages = data.get("query", {}).get("pages") or []
    page = pages[0] if pages else {}
    if not page or page.get("missing") or page.get("invalid"):
        raise NotFound(f"Wikipedia page {title!r} not found on {lang}.wikipedia.org")
    text = (page.get("extract") or "").strip()
    if not text:
        raise NotFound(f"Wikipedia page {title!r} on {lang}.wikipedia.org has no extractable text")
    return page["title"], text
