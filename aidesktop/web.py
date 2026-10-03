"""Text-mode web access: fetch pages as readable text, plus web search.

No API keys needed. Search uses DuckDuckGo's HTML endpoint; fetching
strips boilerplate with a tiny built-in readability pass.
"""

from __future__ import annotations

import html as _html
import re
import urllib.parse
import urllib.request

_FETCH_TIMEOUT = 25
_UA = {"User-Agent": "ai-desktop/0.1 (+https://github.com/Bobbywasabi31/Ai-desktop)"}


def fetch(url: str, max_chars: int = 30_000) -> str:
    """Fetch `url` and return its main text content (HTML stripped)."""
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        raw = resp.read(2_000_000).decode("utf-8", errors="replace")
    return _readable(raw)[:max_chars]


def _readable(html: str) -> str:
    # drop scripts, styles, nav/footer/aside noise
    html = re.sub(r"(?is)<(script|style|nav|footer|aside|header)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    text = re.sub(r"(?is)<[^>]+>", "\n", html)
    text = _html.unescape(text)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if len(ln) > 1]
    # de-duplicate repeated lines (menus, cookie banners)
    seen, out = set(), []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
    return "\n".join(out)


def search(query: str, max_results: int = 8) -> list[dict]:
    """Web search via DuckDuckGo HTML. Returns [{title, url, snippet}]."""
    params = urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(f"https://html.duckduckgo.com/html/?{params}", headers=_UA)
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        html = resp.read(1_000_000).decode("utf-8", errors="replace")
    results = []
    for m in re.finditer(
        r'(?is)<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        html,
    ):
        raw_url, title, snippet = m.groups()
        # duckduckgo wraps outbound links in a redirect; unwrap it
        parsed = urllib.parse.urlparse(raw_url)
        qs = urllib.parse.parse_qs(parsed.query)
        url = qs.get("uddg", [raw_url])[0]
        results.append({
            "title": _clean(title),
            "url": url,
            "snippet": _clean(snippet),
        })
        if len(results) >= max_results:
            break
    return results


def _clean(fragment: str) -> str:
    text = re.sub(r"(?is)<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()
