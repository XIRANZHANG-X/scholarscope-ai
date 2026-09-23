import httpx

from scholarscope.external.ror_match import DEFAULT_MIN_SCORE, match_affiliation


def client(payload, seen: list | None = None) -> httpx.Client:
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def item(ror_id: str, score: float, chosen: bool) -> dict:
    return {
        "score": score,
        "chosen": chosen,
        "matching_type": "SINGLE SEARCH",
        "organization": {"id": f"https://ror.org/{ror_id}"},
    }


def test_returns_the_chosen_candidate_above_the_threshold():
    seen: list = []
    payload = {"items": [item("02e7b5302", 1.0, True), item("008pxsf13", 0.94, False)]}

    match = match_affiliation(client(payload, seen), "Nanyang Technological University, Singapore")

    assert match is not None
    assert (match.ror_id, match.score, match.matching_type) == ("02e7b5302", 1.0, "SINGLE SEARCH")
    assert seen[0].url.params["affiliation"] == "Nanyang Technological University, Singapore"


def test_low_scoring_chosen_candidate_is_rejected():
    payload = {"items": [item("008pxsf13", 0.62, True), item("02e7b5302", 0.55, False)]}
    assert match_affiliation(client(payload), "Technological University") is None


def test_no_chosen_candidate_means_no_match():
    payload = {"items": [item("008pxsf13", 0.94, False)]}
    assert match_affiliation(client(payload), "Some Unknown Lab") is None


def test_empty_items_and_blank_affiliation():
    seen: list = []
    assert match_affiliation(client({"items": []}, seen), "Nowhere Institute") is None
    assert match_affiliation(client({"items": []}, seen), "   ") is None
    assert len(seen) == 1  # a blank affiliation is not sent to the API


def test_threshold_is_configurable():
    payload = {"items": [item("008pxsf13", 0.7, True)]}
    assert match_affiliation(client(payload), "Partial Name") is None
    match = match_affiliation(client(payload), "Partial Name", min_score=0.65)
    assert match is not None and match.ror_id == "008pxsf13"


def test_a_stronger_unchosen_candidate_is_never_substituted():
    # A malformed response with a second "chosen" entry: the endpoint promises at most one, but the
    # code must still bail out on the first below-threshold "chosen" hit rather than scan onward for
    # a stronger one — a non-chosen alternative is already filtered before reaching that check.
    payload = {"items": [item("008pxsf13", 0.5, True), item("02e7b5302", 0.95, True)]}
    assert match_affiliation(client(payload), "Ambiguous Institute") is None


def test_score_exactly_at_the_threshold_is_accepted():
    payload = {"items": [item("008pxsf13", DEFAULT_MIN_SCORE, True)]}
    match = match_affiliation(client(payload), "Boundary University")
    assert match is not None and match.ror_id == "008pxsf13"
