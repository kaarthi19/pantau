"""Polite HTTP client + shared text/URL helpers.

Every collector shares one ``PoliteSession``: honest UA with a contact email,
20s timeouts, and 0.5s spacing per host so we never hammer a source.
"""
from __future__ import annotations

import time
import re
from html import unescape
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

CONTACT_EMAIL = "pantau-bot@users.noreply.github.com"
# A browser-shaped UA with an honest contact comment. Several publisher feeds
# (Carbon Brief, Mongabay) 403 a bare bot UA; this keeps us readable to them
# while still identifying the project and a contact address.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 "
    f"pantau/2.0 (+{CONTACT_EMAIL})"
)
TIMEOUT = 20
HOST_SPACING = 0.5  # seconds between requests to the same host

# Query params that carry no identity — stripped during URL normalization.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "ref_src", "source", "src", "gh_src", "gh_jid", "fbclid", "gclid",
    "mc_cid", "mc_eid", "cmpid", "cid",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class PoliteSession:
    """A thin wrapper over requests.Session enforcing per-host spacing."""

    def __init__(self, timeout: int = TIMEOUT):
        self.timeout = timeout
        self._last: dict[str, float] = {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _wait(self, host: str) -> None:
        prev = self._last.get(host)
        if prev is not None:
            delta = time.monotonic() - prev
            if delta < HOST_SPACING:
                time.sleep(HOST_SPACING - delta)
        self._last[host] = time.monotonic()

    def get(self, url: str, **kw) -> requests.Response:
        host = urlsplit(url).netloc
        self._wait(host)
        kw.setdefault("timeout", self.timeout)
        return self.session.get(url, **kw)


def normalize_url(url: str) -> str:
    """Canonicalize a URL for dedupe: lowercase scheme/host, drop fragment,
    strip tracking params, drop a trailing slash on the path."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query_pairs.sort()
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


def strip_html(text: str, limit: int = 1200) -> str:
    """Turn an HTML/entity blob into a bounded plain-text summary."""
    if not text:
        return ""
    # Unescape first: several ATSs (Greenhouse) return HTML-escaped HTML, so the
    # tags are entities (&lt;p&gt;) until unescaped, then stripped.
    text = unescape(text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def normalize_title(title: str) -> str:
    """Lowercased, whitespace-collapsed title for near-dupe matching."""
    if not title:
        return ""
    return _WS_RE.sub(" ", title.lower()).strip()
