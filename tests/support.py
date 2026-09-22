"""Shared test doubles: an in-memory OpenAlex /works endpoint and a two-query recall config."""

import re
import tomllib

import httpx

from scholarscope.ingestion.openalex_client import OpenAlexClient
from scholarscope.ingestion.recall import RecallConfig

TWO_QUERY_RECALL_TOML = """
from_date = "2019-01-01"
types = ["article"]
primary_field_ids = []

[[queries]]
key = "llm.large_language_model"
theme = "llm"
phrase = "large language model"

[[queries]]
key = "rag.retrieval_augmented_generation"
theme = "rag"
phrase = "retrieval augmented generation"
"""
TWO_QUERY_RECALL = RecallConfig.from_dict(tomllib.loads(TWO_QUERY_RECALL_TOML))

_PHRASE = re.compile(r'title_and_abstract\.search:"([^"]+)"')


class FakeOpenAlex:
    """Serves canned pages keyed by searched phrase; cursors are '*', 'p1', 'p2', ...

    Group-by requests return `groups[group_by]`, halved when the filter restricts the field.
    """

    def __init__(
        self,
        pages_by_phrase: dict[str, list[list[dict]]],
        *,
        groups: dict[str, list[tuple[str, int]]] | None = None,
        cost_per_page: float = 0.001,
    ) -> None:
        self.pages_by_phrase = pages_by_phrase
        self.groups = groups or {}
        self.cost_per_page = cost_per_page
        self.requests: list[dict[str, str]] = []
        self._failures: dict[tuple[str, str], tuple[int, dict[str, str]]] = {}

    def fail(self, phrase: str, cursor: str, status: int, headers: dict[str, str] | None = None) -> None:
        self._failures[(phrase, cursor)] = (status, headers or {})

    def heal(self) -> None:
        self._failures.clear()

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        phrase = _PHRASE.search(params["filter"]).group(1)
        cursor = params.get("cursor", "*")
        if (phrase, cursor) in self._failures:
            status, headers = self._failures[(phrase, cursor)]
            return httpx.Response(status, headers=headers)
        if "group_by" in params:
            divisor = 2 if "primary_topic.field.id" in params["filter"] else 1
            buckets = [
                {"key": key, "key_display_name": key, "count": count // divisor}
                for key, count in self.groups.get(params["group_by"], [])
            ]
            return httpx.Response(200, json={"meta": {"count": 0, "cost_usd": 0.0001}, "results": [], "group_by": buckets})
        pages = self.pages_by_phrase.get(phrase, [[]])
        index = 0 if cursor == "*" else int(cursor.removeprefix("p"))
        next_cursor = f"p{index + 1}" if index + 1 < len(pages) else None
        body = {
            "meta": {"count": sum(map(len, pages)), "next_cursor": next_cursor, "cost_usd": self.cost_per_page},
            "results": pages[index][: int(params["per_page"])],
        }
        return httpx.Response(200, json=body)

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), base_url="https://api.openalex.org")

    def client(self, **kwargs) -> OpenAlexClient:
        return OpenAlexClient(self.http(), sleep=lambda _seconds: None, **kwargs)
