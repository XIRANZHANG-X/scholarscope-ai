from pathlib import Path

import pytest

from scholarscope.external import loader
from scholarscope.external.ror import iter_csv_rows, parse_organization, short_ror_id
from scholarscope.external.worldbank import CountryProfile
from scholarscope.ingestion import runs
from scholarscope.ingestion.loader import load_works
from scholarscope.ingestion.transform import transform_work

pytestmark = pytest.mark.db

DUMP = Path(__file__).parents[1] / "fixtures" / "ror" / "ror_sample.zip"


@pytest.fixture
def corpus(db, works_page) -> int:
    """The three fixture works, so core.institutions holds Google, Harbin IT and Huawei."""
    run_id = runs.start_run(db, "test", {})
    with db.transaction():
        load_works(db, [transform_work(w) for w in works_page["results"]], run_id=run_id, query_key="rag")
    return run_id


def organizations(*ror_ids: str) -> list:
    wanted = set(ror_ids)
    return [parse_organization(row) for row in iter_csv_rows(DUMP) if short_ror_id(row["id"]) in wanted]


def test_load_ror_organizations_stores_names_location_and_relationships(db, corpus):
    loaded = loader.load_ror_organizations(db, organizations("00njsd438", "02e9yx751"), run_id=corpus)

    assert loaded == 2
    row = db.execute(
        "SELECT display_name, status, established, types, aliases, country_code, city, latitude, wikidata_id "
        "FROM external.ror_organizations WHERE ror_id = '00njsd438'"
    ).fetchone()
    assert row[:3] == ("Google (United States)", "active", 1998)
    assert row[3] == ["company", "funder"] and row[4] == ["Google Research", "Googleplex"]
    assert (row[5], row[6]) == ("US", "Mountain View")
    assert row[7] == pytest.approx(37.38605) and row[8] == "Q95"
    assert db.execute(
        "SELECT count(*) FROM external.ror_relationships WHERE ror_id = '00njsd438'"
    ).fetchone() == (8,)
    assert db.execute(
        "SELECT relationship_type FROM external.ror_relationships "
        "WHERE ror_id = '00njsd438' AND related_ror_id = '02e9yx751'"
    ).fetchone() == ("parent",)


def test_reloading_organizations_replaces_their_relationships(db, corpus):
    loader.load_ror_organizations(db, organizations("00njsd438"), run_id=corpus)
    db.execute(
        "INSERT INTO external.ror_relationships (ror_id, related_ror_id, relationship_type) "
        "VALUES ('00njsd438', '09fake0000', 'related')"
    )

    loader.load_ror_organizations(db, organizations("00njsd438"), run_id=corpus)

    assert db.execute(
        "SELECT count(*) FROM external.ror_relationships WHERE related_ror_id = '09fake0000'"
    ).fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM external.ror_organizations").fetchone() == (1,)


def test_crosswalk_links_institutions_that_openalex_already_matched(db, corpus):
    loader.load_ror_organizations(db, organizations("00njsd438", "01yqg2h08", "00cmhce21"), run_id=corpus)

    linked = loader.link_crosswalk_from_openalex(db, run_id=corpus)

    assert linked == 3
    assert db.execute(
        "SELECT ror_id, match_method, match_score FROM external.institution_crosswalk "
        "WHERE institution_id = 'I1291425158'"
    ).fetchone() == ("00njsd438", "openalex", None)
    assert loader.unmatched_institutions(db) == []


def test_crosswalk_skips_institutions_whose_ror_record_is_absent(db, corpus):
    loader.load_ror_organizations(db, organizations("00njsd438"), run_id=corpus)

    assert loader.link_crosswalk_from_openalex(db, run_id=corpus) == 1
    assert [row[0] for row in loader.unmatched_institutions(db)] == ["I204983213", "I2250955327"]


def test_name_matched_institution_is_recorded_with_its_score(db, corpus):
    loader.load_ror_organizations(db, organizations("02e7b5302"), run_id=corpus)
    db.execute(
        "INSERT INTO core.institutions (institution_id, display_name, ror_id) VALUES ('I99', 'NTU', NULL)"
    )

    loader.link_crosswalk_match(db, "I99", "02e7b5302", 0.93, run_id=corpus)

    row = db.execute(
        "SELECT ror_id, match_method, match_score FROM external.institution_crosswalk "
        "WHERE institution_id = 'I99'"
    ).fetchone()
    assert row[:2] == ("02e7b5302", "affiliation_string")
    assert row[2] == pytest.approx(0.93)


def test_institution_relationships_view_keeps_pairs_inside_the_corpus(db, corpus):
    loader.load_ror_organizations(db, organizations("00njsd438", "02e9yx751"), run_id=corpus)
    db.execute(
        "INSERT INTO core.institutions (institution_id, display_name, ror_id) "
        "VALUES ('I4210128969', 'Alphabet (United States)', '02e9yx751')"
    )
    loader.link_crosswalk_from_openalex(db, run_id=corpus)

    rows = db.execute(
        "SELECT institution_id, related_institution_id, relationship_type FROM bridge.institution_relationships "
        "ORDER BY institution_id"
    ).fetchall()

    assert ("I1291425158", "I4210128969", "parent") in rows
    assert ("I4210128969", "I1291425158", "child") in rows
    assert all(row[1] is not None for row in rows)  # targets outside the corpus never appear


def test_affiliation_matches_cache_records_hits_and_confident_misses(db, corpus, works_page):
    from scholarscope.external.ror_match import AffiliationMatch

    loader.load_ror_organizations(db, organizations("02e7b5302"), run_id=corpus)
    results = [
        ("Nanyang Technological University, Singapore", AffiliationMatch("02e7b5302", 1.0, "SINGLE SEARCH")),
        ("A lab that ROR does not know", None),
    ]

    stored = loader.load_affiliation_matches(db, results, run_id=corpus)

    assert stored == 2
    assert db.execute(
        "SELECT ror_id, match_score FROM external.affiliation_matches WHERE affiliation LIKE 'Nanyang%'"
    ).fetchone() == ("02e7b5302", pytest.approx(1.0))
    assert db.execute(
        "SELECT ror_id, match_score FROM external.affiliation_matches WHERE affiliation LIKE 'A lab%'"
    ).fetchone() == (None, None)


def test_recovered_affiliations_give_a_work_its_country(db, works_page):
    """An authorship OpenAlex left unlinked gains a country through the ROR match."""
    from scholarscope.external.ror_match import AffiliationMatch

    preprint = next(w for w in works_page["results"] if w["id"].endswith("W4389984066"))
    preprint["authorships"][0]["raw_affiliation_strings"] = ["Nanyang Technological University, Singapore"]
    run_id = runs.start_run(db, "test", {})
    with db.transaction():
        load_works(db, [transform_work(preprint)], run_id=run_id, query_key="rag")
    loader.load_ror_organizations(db, organizations("02e7b5302"), run_id=run_id)
    loader.load_affiliation_matches(
        db,
        [("Nanyang Technological University, Singapore", AffiliationMatch("02e7b5302", 1.0, "SINGLE SEARCH"))],
        run_id=run_id,
    )

    assert db.execute(
        "SELECT work_id, author_seq, ror_id FROM bridge.authorship_ror_institutions"
    ).fetchall() == [("W4389984066", 0, "02e7b5302")]
    assert db.execute("SELECT work_id, country_code FROM bridge.work_countries").fetchall() == [
        ("W4389984066", "SG")
    ]


def test_country_profiles_fill_the_country_dimension(db, corpus):
    profiles = [
        CountryProfile("SG", "SGP", "Singapore", "East Asia & Pacific", "High income", "Singapore", 1.28, 103.8),
        CountryProfile("CN", "CHN", "China", "East Asia & Pacific", "Upper middle income", "Beijing", 39.9, 116.3),
    ]

    assert loader.load_country_profiles(db, profiles, run_id=corpus) == 2
    assert db.execute("SELECT name FROM core.countries WHERE country_code = 'SG'").fetchone() == ("Singapore",)
    assert db.execute(
        "SELECT iso3_code, region, income_level FROM external.country_profiles WHERE country_code = 'CN'"
    ).fetchone() == ("CHN", "East Asia & Pacific", "Upper middle income")


def test_observations_keep_nulls_and_skip_unknown_countries(db, corpus):
    loader.load_country_profiles(
        db, [CountryProfile("SG", "SGP", "Singapore", None, None, None, None, None)], run_id=corpus
    )
    loader.load_indicator_catalogue(db, {"SP.POP.TOTL": "Population, total"})
    observations = [
        {"country_code": "SG", "indicator_code": "SP.POP.TOTL", "year": 2024, "value": 6.04e6},
        {"country_code": "SG", "indicator_code": "SP.POP.TOTL", "year": 2026, "value": None},
        {"country_code": "ZZ", "indicator_code": "SP.POP.TOTL", "year": 2024, "value": 1.0},
    ]

    stored = loader.load_observations(db, observations, run_id=corpus)

    assert stored == 2  # the unknown country code is dropped rather than breaking the foreign key
    assert db.execute(
        "SELECT value FROM external.country_indicators WHERE country_code = 'SG' AND year = 2026"
    ).fetchone() == (None,)


def test_missing_ror_ids_reports_only_what_is_absent(db, corpus):
    loader.load_ror_organizations(db, organizations("00njsd438"), run_id=corpus)
    assert loader.missing_ror_ids(db, {"00njsd438", "02e7b5302"}) == {"02e7b5302"}
    assert loader.missing_ror_ids(db, set()) == set()
