"""World Bank Open Data v2: country metadata and indicator series. No API key, no cost.

Coverage measured 2026-09-22 over 217 countries: population is complete through 2025, GDP ~96%
through 2024, but R&D spending and researchers-per-million only reach ~40% of countries and stop at
2023. Values are therefore stored as they come, NULLs included, and the analysis layer joins the
most recent year at or before the year it needs.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import httpx

BASE_URL = "https://api.worldbank.org/v2"
PER_PAGE = 20000

# Architecture §4.3. The name is stored so reports can label a series without a second lookup.
INDICATORS = {
    "NY.GDP.MKTP.CD": "GDP (current US$)",
    "NY.GDP.PCAP.CD": "GDP per capita (current US$)",
    "SP.POP.TOTL": "Population, total",
    "GB.XPD.RSDV.GD.ZS": "Research and development expenditure (% of GDP)",
    "SP.POP.SCIE.RD.P6": "Researchers in R&D (per million people)",
    "TX.VAL.TECH.MF.ZS": "High-technology exports (% of manufactured exports)",
}


class WorldBankError(RuntimeError):
    """The API returned an error payload or an unusable response."""


@dataclass(frozen=True)
class CountryProfile:
    country_code: str  # ISO 3166-1 alpha-2, matching core.countries
    iso3_code: str
    name: str
    region: str | None
    income_level: str | None
    capital_city: str | None
    latitude: float | None
    longitude: float | None


def _float(value: str | None) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _pages(http: httpx.Client, path: str, params: dict) -> Iterator[list]:
    """Yield each page's data array, following the envelope's page count."""
    page = 1
    while True:
        query = {"format": "json", "per_page": PER_PAGE, "page": page, **params}
        response = http.get(f"{BASE_URL}/{path}", params=query)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, list) or len(body) < 2:
            message = body[0].get("message") if isinstance(body, list) and body else body
            raise WorldBankError(f"World Bank error for {path}: {message}")
        envelope, data = body[0], body[1] or []
        yield data
        if page >= int(envelope.get("pages") or 1):
            return
        page += 1


def fetch_countries(http: httpx.Client) -> list[CountryProfile]:
    """Real countries only: the endpoint also returns 78 aggregates, which carry region id 'NA'."""
    profiles = []
    for data in _pages(http, "country", {}):
        for row in data:
            if (row.get("region") or {}).get("id") == "NA" or not row.get("iso2Code"):
                continue
            profiles.append(
                CountryProfile(
                    country_code=row["iso2Code"].strip().upper(),
                    iso3_code=row["id"].strip().upper(),
                    name=row["name"].strip(),
                    region=((row.get("region") or {}).get("value") or "").strip() or None,
                    income_level=((row.get("incomeLevel") or {}).get("value") or "").strip() or None,
                    capital_city=(row.get("capitalCity") or "").strip() or None,
                    latitude=_float(row.get("latitude")),
                    longitude=_float(row.get("longitude")),
                )
            )
    return profiles


def fetch_indicator(http: httpx.Client, indicator_code: str, *, from_year: int, to_year: int) -> list[dict]:
    """Observations for one indicator; a missing value stays NULL rather than being dropped."""
    observations = []
    for data in _pages(http, f"country/all/indicator/{indicator_code}", {"date": f"{from_year}:{to_year}"}):
        for row in data:
            country_code = (row.get("country") or {}).get("id", "").strip().upper()
            if not country_code or not row.get("date"):
                continue
            observations.append(
                {
                    "country_code": country_code,
                    "indicator_code": indicator_code,
                    "year": int(row["date"]),
                    "value": _float(row.get("value")),
                }
            )
    return observations
