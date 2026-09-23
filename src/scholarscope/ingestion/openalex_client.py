"""Minimal OpenAlex REST client: cursor paging, per-call cost tracking, retry honouring Retry-After."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt


class OpenAlexError(RuntimeError):
    """Non-retryable API error."""


class RetryableResponseError(OpenAlexError):
    def __init__(self, status_code: int, retry_after: float | None) -> None:
        super().__init__(f"OpenAlex returned {status_code}")
        self.status_code = status_code
        self.retry_after = retry_after


class BudgetExhaustedError(OpenAlexError):
    """HTTP 429 whose Retry-After is too long to wait for: the daily budget is spent."""


@dataclass(frozen=True)
class Page:
    results: list[dict]
    next_cursor: str | None
    count: int
    cost_usd: float
    raw: dict


@dataclass(frozen=True)
class GroupCount:
    key: str
    count: int


def _parse_retry_after(value: str | None) -> float | None:
    """RFC 7231: either delay-seconds ("120") or an HTTP-date. Negative/past values clamp to 0.0."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return max(seconds, 0.0)


class OpenAlexClient:
    def __init__(
        self,
        http: httpx.Client,
        api_key: str | None = None,
        *,
        max_attempts: int = 5,
        max_retry_after_s: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._max_attempts = max_attempts
        self._max_retry_after_s = max_retry_after_s
        self._sleep = sleep

    def fetch_works_page(self, filter_: str, cursor: str = "*", per_page: int = 100, sort: str | None = None) -> Page:
        params = {"filter": filter_, "cursor": cursor, "per_page": per_page}
        if sort:
            params["sort"] = sort
        body = self._get("/works", params)
        meta = body["meta"]
        return Page(
            results=body["results"],
            next_cursor=meta.get("next_cursor"),
            count=int(meta["count"]),
            cost_usd=float(meta.get("cost_usd") or 0.0),
            raw=body,
        )

    def group_works(self, filter_: str, group_by: str) -> tuple[list[GroupCount], float]:
        """Counts per `group_by` bucket, and the call's cost in USD."""
        body = self._get("/works", {"filter": filter_, "group_by": group_by, "per_page": 200})
        groups = [GroupCount(str(g.get("key_display_name") or g["key"]), int(g["count"])) for g in body["group_by"]]
        return groups, float(body["meta"].get("cost_usd") or 0.0)

    def _get(self, path: str, params: dict) -> dict:
        retrying = Retrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type((RetryableResponseError, httpx.TransportError)),
            sleep=self._sleep,
            reraise=True,
        )
        return retrying(self._get_once, path, params)

    def _get_once(self, path: str, params: dict) -> dict:
        response = self._http.get(path, params=params, headers=self._headers)
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            if response.status_code == 429 and retry_after is not None and retry_after > self._max_retry_after_s:
                raise BudgetExhaustedError(f"OpenAlex budget exhausted; retry after {retry_after:.0f}s")
            raise RetryableResponseError(response.status_code, retry_after)
        if response.status_code >= 400:
            raise OpenAlexError(f"OpenAlex returned {response.status_code}: {response.text[:300]}")
        return response.json()

    @staticmethod
    def _wait(state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, RetryableResponseError) and exc.retry_after is not None:
            return exc.retry_after
        return min(2.0**state.attempt_number, 30.0)
