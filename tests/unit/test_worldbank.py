import json
from pathlib import Path

import httpx
import pytest

from scholarscope.external.worldbank import INDICATORS, WorldBankError, fetch_countries, fetch_indicator

FIXTURES = Path(__file__).parents[1] / "fixtures" / "worldbank"


def client(payload, seen: list | None = None) -> httpx.Client:
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_countries_drops_aggregates_and_maps_iso2():
    payload = json.loads((FIXTURES / "worldbank_countries.json").read_text(encoding="utf-8"))
    seen: list = []

    profiles = fetch_countries(client(payload, seen))

    assert {p.country_code for p in profiles} == {"CN", "US", "SG", "IN"}  # the aggregate row is gone
    singapore = next(p for p in profiles if p.country_code == "SG")
    assert (singapore.iso3_code, singapore.name) == ("SGP", "Singapore")
    assert singapore.income_level == "High income"
    assert singapore.region == "East Asia & Pacific"
    assert singapore.capital_city == "Singapore"
    assert singapore.latitude == pytest.approx(1.28941) and singapore.longitude == pytest.approx(103.85)
    assert seen[0].url.params["format"] == "json"


def test_fetch_indicator_keeps_missing_values_as_none():
    payload = json.loads((FIXTURES / "worldbank_rd_indicator.json").read_text(encoding="utf-8"))
    seen: list = []

    observations = fetch_indicator(client(payload, seen), "GB.XPD.RSDV.GD.ZS", from_year=2019, to_year=2026)

    assert len(observations) == 28  # 4 countries x 8 years, nothing dropped
    assert {o["indicator_code"] for o in observations} == {"GB.XPD.RSDV.GD.ZS"}
    assert all(isinstance(o["year"], int) for o in observations)
    china_2023 = next(o for o in observations if o["country_code"] == "CN" and o["year"] == 2023)
    assert china_2023["value"] == pytest.approx(2.57729)
    assert any(o["value"] is None for o in observations)  # the reporting lag, preserved
    assert seen[0].url.params["date"] == "2019:2026"
    assert seen[0].url.path.endswith("/country/all/indicator/GB.XPD.RSDV.GD.ZS")


def test_fetch_indicator_follows_pagination():
    pages = {
        "1": [{"page": 1, "pages": 2, "per_page": 1, "total": 2},
              [{"country": {"id": "CN"}, "date": "2024", "value": 1.5}]],
        "2": [{"page": 2, "pages": 2, "per_page": 1, "total": 2},
              [{"country": {"id": "US"}, "date": "2024", "value": 3.5}]],
    }
    requested: list[str] = []

    def handler(request):
        page = request.url.params["page"]
        requested.append(page)
        return httpx.Response(200, json=pages[page])

    observations = fetch_indicator(
        httpx.Client(transport=httpx.MockTransport(handler)), "SP.POP.TOTL", from_year=2024, to_year=2024
    )
    assert requested == ["1", "2"]
    assert [o["country_code"] for o in observations] == ["CN", "US"]


def test_error_payload_raises():
    payload = [{"message": [{"id": "120", "key": "Invalid value", "value": "The provided parameter value is not valid"}]}]
    with pytest.raises(WorldBankError, match="Invalid value"):
        fetch_countries(client(payload))


def test_indicator_catalogue_matches_the_architecture():
    assert set(INDICATORS) == {
        "NY.GDP.MKTP.CD",
        "NY.GDP.PCAP.CD",
        "SP.POP.TOTL",
        "GB.XPD.RSDV.GD.ZS",
        "SP.POP.SCIE.RD.P6",
        "TX.VAL.TECH.MF.ZS",
    }
    assert INDICATORS["SP.POP.SCIE.RD.P6"] == "Researchers in R&D (per million people)"
