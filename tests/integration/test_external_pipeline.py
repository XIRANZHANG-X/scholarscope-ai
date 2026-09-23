import csv
import io
import logging
import zipfile
from pathlib import Path

import httpx
import pytest

from scholarscope.external import pipeline
from scholarscope.external.worldbank import INDICATORS, WorldBankError
from scholarscope.ingestion import runs
from scholarscope.ingestion.loader import load_works
from scholarscope.ingestion.transform import transform_work
from tests.support import FakeRorApi, FakeWorldBankApi, world_bank_country

pytestmark = pytest.mark.db

DUMP = Path(__file__).parents[1] / "fixtures" / "ror" / "ror_sample.zip"
NTU = "Nanyang Technological University, Singapore"
# An organisation registered after the monthly dump was cut: api.ror.org knows it, the snapshot does not.
TOO_NEW = "05n3wer99"


def newer_dump(tmp_path: Path, ror_id: str) -> Path:
    """The sample dump plus one organisation, standing in for next month's snapshot."""
    with zipfile.ZipFile(DUMP) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".csv"))
        rows = list(csv.DictReader(io.StringIO(archive.read(member).decode("utf-8"))))
    extra = {**rows[0], "id": f"https://ror.org/{ror_id}", "names.types.ror_display": "Newly Registered Institute"}
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows([*rows, extra])
    path = tmp_path / "v2.14-2026-10-22-ror-data.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("v2.14-2026-10-22-ror-data.csv", buffer.getvalue())
    return path


@pytest.fixture
def corpus(db, works_page):
    """Three works, and one authorship left unlinked by OpenAlex but carrying a raw affiliation."""
    preprint = next(w for w in works_page["results"] if w["id"].endswith("W4389984066"))
    preprint["authorships"][0]["raw_affiliation_strings"] = [NTU]
    run_id = runs.start_run(db, "test", {})
    with db.transaction():
        load_works(db, [transform_work(w) for w in works_page["results"]], run_id=run_id, query_key="rag")
    return run_id


@pytest.fixture
def many_affiliations(db, corpus) -> list[str]:
    """200 more distinct unlinked affiliation strings, enough to span several flushes."""
    strings = [f"Institute {number:03d}" for number in range(200)]
    db.execute(
        "INSERT INTO bridge.work_authors (work_id, author_seq, raw_author_name, raw_affiliation_strings) "
        "VALUES ('W4389984066', 50, 'Test Author', %s)",
        (strings,),
    )
    return strings


def scalar(db, query: str):
    return db.execute(query).fetchone()[0]


def test_enrich_ror_loads_the_subset_links_and_matches(db, corpus, tmp_path):
    api = FakeRorApi({NTU: ("02e7b5302", 1.0)})

    result = pipeline.enrich_ror(
        db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None
    )

    assert result.version == "ror_sample.zip"  # a local dump keeps its file name as the version
    assert result.institutions_linked == 3  # Google, Harbin IT, Huawei came with ROR ids
    assert (result.affiliations_matched, result.affiliations_unmatched) == (1, 0)
    assert result.organizations_loaded == 4  # three from the corpus plus NTU, pulled in by the match
    assert scalar(db, "SELECT count(*) FROM external.institution_crosswalk") == 3
    assert scalar(db, "SELECT count(*) FROM external.ror_organizations WHERE ror_id = '02e7b5302'") == 1
    assert scalar(db, "SELECT count(*) FROM bridge.authorship_ror_institutions") == 1
    assert ("W4389984066", "SG") in db.execute("SELECT work_id, country_code FROM bridge.work_countries").fetchall()
    assert api.requests == [NTU]


def test_enrich_ror_records_the_release_in_the_run(db, corpus, tmp_path):
    result = pipeline.enrich_ror(
        db, FakeRorApi({}).http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None
    )

    row = db.execute(
        "SELECT source_id, profile, status, params FROM meta.ingestion_runs WHERE run_id = %s", (result.run_id,)
    ).fetchone()
    assert row[:3] == ("ror", "dump", "succeeded")
    assert row[3]["file_name"] == "ror_sample.zip"
    assert len(row[3]["sha256"]) == 64
    assert row[3]["min_score"] == pytest.approx(0.8)


def test_enrich_ror_is_idempotent_and_reuses_the_match_cache(db, corpus, tmp_path):
    api = FakeRorApi({NTU: ("02e7b5302", 1.0)})
    first = pipeline.enrich_ror(db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None)

    second = pipeline.enrich_ror(db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None)

    assert second.institutions_linked == first.institutions_linked
    assert second.affiliations_matched == 0  # the string is cached, so ROR is not asked again
    assert api.requests == [NTU]
    assert scalar(db, "SELECT count(*) FROM external.ror_organizations") == 4
    assert scalar(db, "SELECT count(*) FROM external.affiliation_matches") == 1


def test_enrich_ror_caches_a_confident_no_match(db, corpus, tmp_path):
    result = pipeline.enrich_ror(
        db, FakeRorApi({}).http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None
    )

    assert (result.affiliations_matched, result.affiliations_unmatched) == (0, 1)
    assert db.execute(
        "SELECT ror_id, match_score FROM external.affiliation_matches WHERE affiliation = %s", (NTU,)
    ).fetchone() == (None, None)
    assert scalar(db, "SELECT count(*) FROM bridge.authorship_ror_institutions") == 0


def test_enrich_ror_can_skip_affiliation_matching(db, corpus, tmp_path):
    api = FakeRorApi({NTU: ("02e7b5302", 1.0)})

    result = pipeline.enrich_ror(
        db, api.http(), dump_dir=tmp_path, dump_path=DUMP, match_affiliations=False, sleep=lambda _s: None
    )

    assert (result.affiliations_matched, result.affiliations_unmatched) == (0, 0)
    assert api.requests == []
    assert scalar(db, "SELECT count(*) FROM external.affiliation_matches") == 0


def test_enrich_ror_matches_an_institution_openalex_left_without_a_ror_id(db, corpus, tmp_path):
    db.execute("INSERT INTO core.institutions (institution_id, display_name, ror_id) VALUES ('I99', 'NTU', NULL)")
    api = FakeRorApi({"NTU": ("02e7b5302", 0.91)})

    result = pipeline.enrich_ror(
        db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None
    )

    assert result.institutions_linked == 4  # the three OpenAlex ids plus the name-matched one
    row = db.execute(
        "SELECT ror_id, match_method, match_score FROM external.institution_crosswalk WHERE institution_id = 'I99'"
    ).fetchone()
    assert row[:2] == ("02e7b5302", "affiliation_string")
    assert row[2] == pytest.approx(0.91)


def test_affiliation_match_on_an_organisation_the_dump_lacks_is_skipped(db, corpus, tmp_path, caplog):
    """ROR's API is live, the dump is a monthly snapshot; a newer organisation must not kill the batch."""
    api = FakeRorApi({NTU: (TOO_NEW, 0.99)})

    with caplog.at_level(logging.WARNING):
        result = pipeline.enrich_ror(db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None)

    assert result.affiliations_matched == 0
    assert scalar(db, "SELECT count(*) FROM external.affiliation_matches") == 0  # not even as a no-match
    assert TOO_NEW in caplog.text

    later = pipeline.enrich_ror(
        db, FakeRorApi({NTU: (TOO_NEW, 0.99)}).http(),
        dump_dir=tmp_path, dump_path=newer_dump(tmp_path, TOO_NEW), sleep=lambda _s: None,
    )

    assert later.affiliations_matched == 1  # nothing was cached, so the newer dump gets a second chance
    assert db.execute(
        "SELECT ror_id FROM external.affiliation_matches WHERE affiliation = %s", (NTU,)
    ).fetchone() == (TOO_NEW,)


def test_institution_name_match_on_an_organisation_the_dump_lacks_is_skipped(db, corpus, tmp_path, caplog):
    db.execute("INSERT INTO core.institutions (institution_id, display_name, ror_id) VALUES ('I99', 'NTU', NULL)")
    api = FakeRorApi({"NTU": (TOO_NEW, 0.95)})

    with caplog.at_level(logging.WARNING):
        result = pipeline.enrich_ror(db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None)

    assert result.institutions_linked == 3  # the three OpenAlex ids; I99 stays unlinked for now
    assert scalar(db, "SELECT count(*) FROM external.institution_crosswalk WHERE institution_id = 'I99'") == 0
    assert TOO_NEW in caplog.text

    pipeline.enrich_ror(
        db, FakeRorApi({"NTU": (TOO_NEW, 0.95)}).http(),
        dump_dir=tmp_path, dump_path=newer_dump(tmp_path, TOO_NEW), sleep=lambda _s: None,
    )

    assert db.execute(
        "SELECT ror_id FROM external.institution_crosswalk WHERE institution_id = 'I99'"
    ).fetchone() == (TOO_NEW,)


def test_affiliation_matching_keeps_completed_flushes_when_ror_dies(db, corpus, many_affiliations, tmp_path):
    """A dying API must not discard the strings already asked about: the next run skips them."""
    api = FakeRorApi({}, fail_after=149)

    with pytest.raises(httpx.ConnectError):
        pipeline.enrich_ror(db, api.http(), dump_dir=tmp_path, dump_path=DUMP, sleep=lambda _s: None)

    assert len(api.requests) == 150
    assert scalar(db, "SELECT count(*) FROM external.affiliation_matches") == 100  # one full flush
    assert scalar(db, "SELECT status FROM meta.ingestion_runs ORDER BY run_id DESC LIMIT 1") == "failed"


def test_failing_ror_run_is_marked_failed_and_reraised(db, corpus, tmp_path):
    from scholarscope.external.ror import RorDumpError

    empty = tmp_path / "empty.zip"
    import zipfile

    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("readme.txt", "not a dump")

    with pytest.raises(RorDumpError):
        pipeline.enrich_ror(db, FakeRorApi({}).http(), dump_dir=tmp_path, dump_path=empty, sleep=lambda _s: None)

    assert scalar(db, "SELECT status FROM meta.ingestion_runs ORDER BY run_id DESC LIMIT 1") == "failed"


def world_bank_api(values: dict | None = None) -> FakeWorldBankApi:
    countries = [
        world_bank_country("SG", "SGP", "Singapore"),
        world_bank_country("CN", "CHN", "China"),
        {"id": "AFE", "iso2Code": "ZH", "name": "Africa Eastern and Southern",
         "region": {"id": "NA", "iso2code": "", "value": "Aggregates"},
         "incomeLevel": {"id": "NA", "value": "Aggregates"}, "capitalCity": "", "longitude": "", "latitude": ""},
    ]
    if values is None:
        values = {code: [("SG", 2024, 1.0), ("CN", 2024, 2.0), ("SG", 2026, None)] for code in INDICATORS}
    return FakeWorldBankApi(countries, values)


def test_enrich_worldbank_stores_countries_indicators_and_nulls(db, corpus):
    api = world_bank_api()

    result = pipeline.enrich_worldbank(db, api.http(), from_year=2019, to_year=2026)

    assert result.countries == 2  # the aggregate row is excluded
    assert result.observations == len(INDICATORS) * 3
    assert result.missing_values == len(INDICATORS)  # one NULL per indicator
    assert scalar(db, "SELECT name FROM core.countries WHERE country_code = 'SG'") == "Singapore"
    assert scalar(db, "SELECT count(*) FROM external.indicators") == len(INDICATORS)
    assert db.execute(
        "SELECT value FROM external.country_indicators "
        "WHERE country_code = 'SG' AND indicator_code = 'SP.POP.TOTL' AND year = 2026"
    ).fetchone() == (None,)
    assert scalar(db, "SELECT status FROM meta.ingestion_runs WHERE run_id = %s" % result.run_id) == "succeeded"
    assert scalar(db, "SELECT source_id FROM meta.ingestion_runs WHERE run_id = %s" % result.run_id) == "worldbank"


def test_enrich_worldbank_counts_missing_values_only_among_stored_rows(db, corpus):
    """'ZH' is the aggregate Africa Eastern and Southern: reported by the indicator endpoint, not a
    country, so its NULL is never stored and must not inflate the missing-value count."""
    api = world_bank_api({code: [("SG", 2026, None), ("ZH", 2026, None)] for code in INDICATORS})

    result = pipeline.enrich_worldbank(db, api.http(), from_year=2019, to_year=2026)

    assert result.observations == len(INDICATORS)
    assert result.missing_values == len(INDICATORS)


def test_enrich_worldbank_is_idempotent(db, corpus):
    first = pipeline.enrich_worldbank(db, world_bank_api().http(), from_year=2019, to_year=2026)
    before = scalar(db, "SELECT count(*) FROM external.country_indicators")

    second = pipeline.enrich_worldbank(db, world_bank_api().http(), from_year=2019, to_year=2026)

    assert scalar(db, "SELECT count(*) FROM external.country_indicators") == before
    assert second.observations == first.observations
    assert scalar(
        db, "SELECT count(DISTINCT run_id) FROM external.country_indicators"
    ) == 1  # every row now points at the newer run


def test_failing_worldbank_run_is_marked_failed_and_reraised(db, corpus):
    api = world_bank_api()
    api.fail_indicator = "SP.POP.TOTL"

    with pytest.raises(WorldBankError):
        pipeline.enrich_worldbank(db, api.http(), from_year=2019, to_year=2026)

    assert scalar(db, "SELECT status FROM meta.ingestion_runs ORDER BY run_id DESC LIMIT 1") == "failed"
