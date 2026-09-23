"""ROR's affiliation-matching endpoint, for the institutions OpenAlex left without a ROR id.

The endpoint scores candidates and marks at most one `chosen`. Only that candidate is considered,
and only above a score threshold: a wrong institution is worse for country and institution analysis
than an admitted gap, which the quality metrics report either way.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from scholarscope.external.ror import short_ror_id

MATCH_URL = "https://api.ror.org/v2/organizations"
DEFAULT_MIN_SCORE = 0.8


@dataclass(frozen=True)
class AffiliationMatch:
    ror_id: str
    score: float
    matching_type: str


def match_affiliation(
    http: httpx.Client, affiliation: str, *, min_score: float = DEFAULT_MIN_SCORE
) -> AffiliationMatch | None:
    """The chosen ROR organisation for a raw affiliation string, or None when none is confident."""
    if not affiliation.strip():
        return None
    response = http.get(MATCH_URL, params={"affiliation": affiliation})
    response.raise_for_status()
    for item in response.json().get("items", []):
        if not item.get("chosen"):
            continue
        score = float(item.get("score") or 0.0)
        ror_id = short_ror_id((item.get("organization") or {}).get("id"))
        if ror_id and score >= min_score:
            return AffiliationMatch(ror_id=ror_id, score=score, matching_type=item.get("matching_type", ""))
        return None  # the endpoint chose this one; a weaker candidate is not a better answer
    return None
