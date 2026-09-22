import httpx
import pytest

from scholarscope.ingestion.openalex_client import BudgetExhaustedError, OpenAlexClient, OpenAlexError


def make_client(handler, **kwargs) -> tuple[OpenAlexClient, list[float]]:
    sleeps: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.openalex.org")
    return OpenAlexClient(http, sleep=sleeps.append, **kwargs), sleeps


def page_body(results, next_cursor=None, cost=0.001) -> dict:
    return {"meta": {"count": 42, "next_cursor": next_cursor, "cost_usd": cost}, "results": results}


def test_fetch_page_sends_filter_cursor_and_bearer_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=page_body([{"id": "https://openalex.org/W1"}], next_cursor="abc"))

    client, _ = make_client(handler, api_key="k3y")
    page = client.fetch_works_page("type:article", cursor="*", per_page=50, sort="publication_date:asc")

    assert page.results == [{"id": "https://openalex.org/W1"}]
    assert (page.next_cursor, page.count, page.cost_usd) == ("abc", 42, 0.001)
    request = seen[0]
    assert request.url.path == "/works"
    assert request.url.params["filter"] == "type:article"
    assert (request.url.params["cursor"], request.url.params["per_page"]) == ("*", "50")
    assert request.url.params["sort"] == "publication_date:asc"
    assert request.headers["Authorization"] == "Bearer k3y"
    assert "k3y" not in str(request.url)


def test_without_key_no_authorization_header():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=page_body([]))

    client, _ = make_client(handler)
    client.fetch_works_page("type:article")
    assert "Authorization" not in seen[0].headers
    assert "sort" not in seen[0].url.params


def test_retries_server_error_and_short_429_honouring_retry_after():
    responses = iter([
        httpx.Response(503),
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json=page_body([])),
    ])
    client, sleeps = make_client(lambda request: next(responses))
    assert client.fetch_works_page("type:article").results == []
    assert sleeps == [2.0, 7.0]


def test_retries_transport_errors():
    outcomes = iter([httpx.ConnectError("connection reset"), None])

    def handler(request):
        error = next(outcomes)
        if error:
            raise error
        return httpx.Response(200, json=page_body([]))

    client, sleeps = make_client(handler)
    assert client.fetch_works_page("type:article").results == []
    assert len(sleeps) == 1


def test_long_retry_after_means_budget_exhausted():
    client, sleeps = make_client(lambda request: httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(BudgetExhaustedError):
        client.fetch_works_page("type:article")
    assert sleeps == []


def test_gives_up_after_max_attempts():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    client, _ = make_client(handler, max_attempts=3)
    with pytest.raises(OpenAlexError):
        client.fetch_works_page("type:article")
    assert len(calls) == 3


def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, text="invalid filter")

    client, _ = make_client(handler)
    with pytest.raises(OpenAlexError, match="400"):
        client.fetch_works_page("nonsense")
    assert len(calls) == 1


def test_group_works_uses_display_keys_and_reports_cost():
    seen = []
    body = {
        "meta": {"count": 3, "cost_usd": 0.0001},
        "results": [],
        "group_by": [
            {"key": "https://openalex.org/types/article", "key_display_name": "article", "count": 2},
            {"key": "https://openalex.org/types/preprint", "key_display_name": "preprint", "count": 1},
        ],
    }

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=body)

    client, _ = make_client(handler)
    groups, cost = client.group_works("type:article|preprint", "type")
    assert [(g.key, g.count) for g in groups] == [("article", 2), ("preprint", 1)]
    assert cost == 0.0001
    assert seen[0].url.params["group_by"] == "type"
