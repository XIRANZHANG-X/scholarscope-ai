"""The ROR data dump: resolve the latest Zenodo release, download it, and read its rows.

ROR publishes a versioned CC0 dump on Zenodo (141,528 organisations in v2.13). One 37 MB download
beats tens of thousands of API calls and is reproducible: version plus SHA-256 pin exactly which
records a run used. The dump's CSV is 55 MB against 318 MB for the JSON and carries every field we
need, so this module reads the CSV member.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ZENODO_RECORDS_URL = "https://zenodo.org/api/records"
ROR_URL_PREFIX = "https://ror.org/"
RELATIONSHIP_TYPES = ("parent", "child", "related", "successor", "predecessor")
_CHUNK = 1 << 20


class RorDumpError(RuntimeError):
    """The Zenodo release could not be resolved or the dump is not readable."""


@dataclass(frozen=True)
class RorRelease:
    version: str  # e.g. "v2.13-2026-09-22"
    doi: str
    file_name: str
    url: str
    size_bytes: int


@dataclass(frozen=True)
class OrganizationRows:
    organization: dict
    relationships: list[dict] = field(default_factory=list)


def latest_release(http: httpx.Client) -> RorRelease:
    """The newest release in Zenodo's `ror-data` community."""
    response = http.get(ZENODO_RECORDS_URL, params={"communities": "ror-data", "sort": "newest", "size": 1})
    response.raise_for_status()
    hits = response.json()["hits"]["hits"]
    if not hits:
        raise RorDumpError("Zenodo returned no releases in the ror-data community")
    record = hits[0]
    archives = [f for f in record.get("files", []) if f["key"].endswith(".zip")]
    if not archives:
        raise RorDumpError(f"Zenodo record {record.get('id')} has no .zip file")
    archive = archives[0]
    return RorRelease(
        version=archive["key"].removesuffix("-ror-data.zip"),
        doi=record.get("doi", ""),
        file_name=archive["key"],
        url=archive["links"]["self"],
        size_bytes=int(archive["size"]),
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(http: httpx.Client, release: RorRelease, directory: Path) -> tuple[Path, str]:
    """Download the dump unless it is already there; returns its path and SHA-256."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / release.file_name
    if not path.exists():
        tmp = path.with_suffix(".part")
        with http.stream("GET", release.url) as response, tmp.open("wb") as fh:
            response.raise_for_status()
            for chunk in response.iter_bytes(_CHUNK):
                fh.write(chunk)
        tmp.replace(path)
    return path, sha256_of(path)


def iter_csv_rows(dump_path: Path) -> Iterator[dict[str, str]]:
    """Yield the dump's CSV rows one at a time (the file is too large to hold in memory)."""
    with zipfile.ZipFile(dump_path) as archive:
        names = [n for n in archive.namelist() if n.endswith(".csv")]
        if not names:
            raise RorDumpError(f"{dump_path.name} contains no CSV member")
        with archive.open(names[0]) as fh:
            yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))


def short_ror_id(value: str | None) -> str | None:
    """'https://ror.org/00njsd438' -> '00njsd438'."""
    if not value:
        return None
    return value.rsplit("/", 1)[-1].strip() or None


def _split(value: str, separator: str = "; ") -> list[str]:
    return [part.strip() for part in value.split(separator) if part.strip()] if value else []


def _strip_language(name: str) -> str:
    """Names are prefixed with their language: 'en: Google Research', 'no_lang_code: Googleplex'."""
    head, separator, tail = name.partition(": ")
    if separator and (head == "no_lang_code" or (head.isalpha() and len(head) <= 3)):
        return tail.strip()
    return name.strip()


def _names(value: str) -> list[str]:
    return [stripped for stripped in (_strip_language(n) for n in _split(value)) if stripped]


def _float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_relationships(row: dict[str, str]) -> list[dict]:
    """'child: url1, url2; parent: url3' -> one row per (ror_id, related_ror_id, type)."""
    ror_id = short_ror_id(row["id"])
    parsed: dict[tuple[str, str], dict] = {}
    for group in _split(row.get("relationships", "")):
        label, separator, targets = group.partition(":")
        relationship_type = label.strip().lower()
        if not separator or relationship_type not in RELATIONSHIP_TYPES:
            continue
        for target in _split(targets, ","):
            related = short_ror_id(target)
            if related:
                parsed[(related, relationship_type)] = {
                    "ror_id": ror_id,
                    "related_ror_id": related,
                    "relationship_type": relationship_type,
                }
    return list(parsed.values())


def parse_organization(row: dict[str, str]) -> OrganizationRows:
    """One CSV row -> the `external.ror_organizations` row plus its relationship rows."""
    ror_id = short_ror_id(row["id"])
    display_name = row.get("names.types.ror_display", "").strip()
    organization = {
        "ror_id": ror_id,
        "display_name": display_name or ror_id,
        "status": row.get("status", "").strip(),
        "established": _int(row.get("established", "")),
        "types": _split(row.get("types", "")),
        "aliases": _names(row.get("names.types.alias", "")),
        "acronyms": _names(row.get("names.types.acronym", "")),
        "country_code": (row.get("locations.geonames_details.country_code") or "").strip().upper() or None,
        "country_name": row.get("locations.geonames_details.country_name", "").strip() or None,
        "subdivision_name": row.get("locations.geonames_details.country_subdivision_name", "").strip() or None,
        "city": row.get("locations.geonames_details.name", "").strip() or None,
        "latitude": _float(row.get("locations.geonames_details.lat", "")),
        "longitude": _float(row.get("locations.geonames_details.lng", "")),
        "continent_code": row.get("locations.geonames_details.continent_code", "").strip() or None,
        "website": row.get("links.type.website", "").strip() or None,
        "wikidata_id": (row.get("external_ids.type.wikidata.preferred") or "").strip()
        or next(iter(_split(row.get("external_ids.type.wikidata.all", ""), ";")), None),
        "grid_id": (row.get("external_ids.type.grid.preferred") or "").strip() or None,
    }
    return OrganizationRows(organization=organization, relationships=parse_relationships(row))
