"""Real-browser control for agents (optional dependency).

Requires ``pip install ai-desktop[browser]`` (playwright + chromium).
The browser keeps cookies and session state between calls, can fill
forms, click, and read rendered text — the same things a human does.
"""

from __future__ import annotations


class BrowserNotInstalled(RuntimeError):
    pass


class Browser:
    """A persistent Chromium page an agent can drive."""

    def __init__(self, headless: bool = True):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise BrowserNotInstalled(
                "browser support needs the extra: pip install 'ai-desktop[browser]'"
            ) from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()

    # -- navigation & reading ------------------------------------------
    def open(self, url: str, wait: str = "domcontentloaded") -> str:
        """Navigate to `url`; return the page title."""
        self._page.goto(url, wait_until=wait, timeout=45_000)
        return self._page.title()

    @property
    def url(self) -> str:
        return self._page.url

    def text(self, max_chars: int = 30_000) -> str:
        """Rendered body text of the current page."""
        return (self._page.inner_text("body") or "")[:max_chars]

    def screenshot(self, path: str) -> str:
        """Save a PNG screenshot; returns the path."""
        self._page.screenshot(path=path)
        return path

    # -- interaction ----------------------------------------------------
    def click(self, selector: str, timeout: int = 10_000) -> None:
        self._page.click(selector, timeout=timeout)

    def fill(self, selector: str, text: str, timeout: int = 10_000) -> None:
        self._page.fill(selector, text, timeout=timeout)

    def press(self, selector: str, key: str) -> None:
        self._page.press(selector, key)

    def select(self, selector: str, value: str) -> None:
        self._page.select_option(selector, value)

    def wait_for(self, selector: str, timeout: int = 10_000) -> None:
        self._page.wait_for_selector(selector, timeout=timeout)

    def evaluate(self, js: str):
        """Run JavaScript in the page; returns the result."""
        return self._page.evaluate(js)

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._pw.stop()

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
