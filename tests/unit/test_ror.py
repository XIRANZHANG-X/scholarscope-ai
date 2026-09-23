import hashlib
import json
from pathlib import Path

import httpx
import pytest

from scholarscope.external.ror import (
    RorDumpError,
    download,
    iter_csv_rows,
    latest_release,
    parse_organization,
    parse_relationships,
    sha256_of,
    short_ror_id,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ror"
DUMP = FIXTURES / "ror_sample.zip"


def rows_by_id() -> dict[str, dict]:
    return {short_ror_id(row["id"]): row for row in iter_csv_rows(DUMP)}


def test_latest_release_reads_the_zenodo_record():
    payload = json.loads((FIXTURES / "zenodo_latest.json").read_text(encoding="utf-8"))
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    release = latest_release(http)

    assert release.version == "v2.13-2026-09-22"
    assert release.file_name == "v2.13-2026-09-22-ror-data.zip"
    assert release.doi == "10.5281/zenodo.22902037"
    assert release.url.endswith("/content")
    assert release.size_bytes > 0
    assert seen[0].url.params["communities"] == "ror-data"
    assert seen[0].url.params["sort"] == "newest"


def test_latest_release_without_hits_is_an_error():
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"hits": {"hits": []}})))
    with pytest.raises(RorDumpError, match="no releases"):
        latest_release(http)


def test_download_writes_the_file_once_and_reports_its_checksum(tmp_path):
    content = DUMP.read_bytes()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=content)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    release = type("R", (), {"file_name": "dump.zip", "url": "https://example.test/dump.zip"})()

    path, digest = download(http, release, tmp_path)
    assert path == tmp_path / "dump.zip"
    assert digest == hashlib.sha256(content).hexdigest()
    assert list(tmp_path.glob("*.part")) == []

    again, digest_again = download(http, release, tmp_path)
    assert (again, digest_again) == (path, digest)
    assert len(calls) == 1  # the second call reuses the file on disk


def test_iter_csv_rows_streams_the_dump():
    ids = [short_ror_id(row["id"]) for row in iter_csv_rows(DUMP)]
    assert "00njsd438" in ids and "02e7b5302" in ids
    assert len(ids) == len(set(ids)) == 6


def test_iter_csv_rows_rejects_an_archive_without_csv(tmp_path):
    import zipfile

    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as archive:
        archive.writestr("readme.txt", "no data here")
    with pytest.raises(RorDumpError, match="no CSV member"):
        list(iter_csv_rows(broken))


def test_iter_csv_rows_rejects_a_truncated_archive(tmp_path):
    truncated = tmp_path / "half.zip"
    truncated.write_bytes(DUMP.read_bytes()[: DUMP.stat().st_size // 2])
    with pytest.raises(RorDumpError, match="not a readable zip archive"):
        list(iter_csv_rows(truncated))


def test_sha256_of_a_path_that_does_not_exist_is_a_dump_error(tmp_path):
    with pytest.raises(RorDumpError, match="cannot be read"):
        sha256_of(tmp_path / "typo.zip")


def test_parse_organization_of_a_company():
    organization = parse_organization(rows_by_id()["00njsd438"]).organization
    assert organization == {
        "ror_id": "00njsd438",
        "display_name": "Google (United States)",
        "status": "active",
        "established": 1998,
        "types": ["company", "funder"],
        "aliases": ["Google Research", "Googleplex"],
        "acronyms": [],
        "country_code": "US",
        "country_name": "United States",
        "subdivision_name": "California",
        "city": "Mountain View",
        "latitude": 37.38605,
        "longitude": -122.08385,
        "continent_code": "NA",
        "website": "https://www.google.com/",
        "wikidata_id": "Q95",
        "grid_id": "grid.420451.6",
    }


def test_parse_organization_keeps_non_active_status():
    organization = parse_organization(rows_by_id()["04yw47259"]).organization
    assert organization["status"] == "inactive"
    assert organization["display_name"] == "Macmurray College"


def test_parse_relationships_splits_types_and_targets():
    relationships = parse_relationships(rows_by_id()["00njsd438"])
    by_type: dict[str, list[str]] = {}
    for row in relationships:
        assert row["ror_id"] == "00njsd438"
        by_type.setdefault(row["relationship_type"], []).append(row["related_ror_id"])
    assert by_type["parent"] == ["02e9yx751"]
    assert len(by_type["child"]) == 7
    assert "04d06q394" in by_type["child"]


def test_parse_relationships_of_an_organisation_without_any():
    assert parse_relationships(rows_by_id()["04yw47259"]) == []


def test_short_ror_id_and_checksum_helpers(tmp_path):
    assert short_ror_id("https://ror.org/00njsd438") == "00njsd438"
    assert short_ror_id(None) is None
    assert short_ror_id("") is None
    sample = tmp_path / "x.bin"
    sample.write_bytes(b"scholarscope")
    assert sha256_of(sample) == hashlib.sha256(b"scholarscope").hexdigest()
