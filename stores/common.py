"""Shared types and HTTP helpers for the store scrapers."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger(__name__)


@dataclass
class Item:
    """One purchasable thing we can track the stock of.

    `key` must be stable across runs -- it is what the state file uses to
    decide whether something just flipped from out-of-stock to in-stock.
    """

    key: str
    store: str
    name: str
    url: str
    available: bool
    variant: Optional[str] = None
    price: Optional[str] = None
    detection: str = "unknown"  # which parsing strategy produced `available`
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.name} — {self.variant}" if self.variant else self.name


class StoreError(RuntimeError):
    """Raised when a store cannot be scraped at all this cycle."""


def make_session(user_agent: str, timeout: int = 25) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session


def polite_get(
    session: requests.Session,
    url: str,
    *,
    delay: float = 1.5,
    jitter: float = 0.5,
    retries: int = 3,
    timeout: int = 25,
    expect_json: bool = False,
):
    """GET with backoff. Sleeps *before* returning so callers stay well-behaved.

    Returns the Response, or raises StoreError after exhausting retries.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
        except requests.RequestException as exc:  # network-level failure
            last_exc = exc
            log.warning("GET %s failed (attempt %d/%d): %s", url, attempt, retries, exc)
        else:
            if resp.status_code == 200:
                if expect_json and "json" not in resp.headers.get("content-type", ""):
                    raise StoreError(f"{url} did not return JSON")
                time.sleep(delay + random.uniform(0, jitter))
                return resp

            if resp.status_code in (429, 500, 502, 503, 504):
                wait = min(60, (2**attempt) * 2)
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = min(120, int(retry_after))
                log.warning(
                    "GET %s -> HTTP %d, backing off %.0fs (attempt %d/%d)",
                    url,
                    resp.status_code,
                    wait,
                    attempt,
                    retries,
                )
                time.sleep(wait)
                continue

            # 404 and friends: no point retrying
            raise StoreError(f"GET {url} -> HTTP {resp.status_code}")

        time.sleep(min(30, (2**attempt)))

    raise StoreError(f"GET {url} failed after {retries} attempts: {last_exc}")


def matches_watchlist(name: str, watchlist: list[str]) -> bool:
    """Empty watchlist means 'track everything'. Otherwise case-insensitive substring."""
    if not watchlist:
        return True
    lowered = name.lower()
    return any(term.lower() in lowered for term in watchlist)
