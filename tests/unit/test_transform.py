from datetime import UTC, date, datetime

from scholarscope.ingestion.transform import normalize_doi, reconstruct_abstract, short_id, transform_work


def by_id(page: dict, work_id: str) -> dict:
    return next(w for w in page["results"] if w["id"].endswith(work_id))


def test_short_id_and_doi_normalisation():
    assert short_id("https://openalex.org/W4384071683") == "W4384071683"
    assert short_id("https://openalex.org/subfields/1702") == "1702"
    assert short_id(None) is None
    assert normalize_doi("https://doi.org/10.1038/S41586-023-06291-2") == "10.1038/s41586-023-06291-2"
    assert normalize_doi("") is None


def test_reconstruct_abstract_orders_words_and_drops_label():
    index = {"Abstract": [0], "models": [2], "Large": [1], "big": [3, 5], "are": [4]}
    assert reconstruct_abstract(index) == "Large models big are big"
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None


def test_work_row_from_published_article(works_page):
    rows = transform_work(by_id(works_page, "W4384071683"))
    work = rows.work
    assert work["work_id"] == "W4384071683"
    assert work["doi"] == "10.1038/s41586-023-06291-2"
    assert work["title"] == "Large language models encode clinical knowledge"
    assert work["abstract"].startswith("Large language models (LLMs) have demonstrated")
    assert (work["publication_date"], work["publication_year"]) == (date(2023, 7, 12), 2023)
    assert (work["type"], work["source_id"]) == ("article", "S137773608")
    assert (work["is_oa"], work["oa_status"]) == (True, "hybrid")
    assert (work["cited_by_count"], work["referenced_works_count"]) == (3855, 91)
    assert (work["citation_percentile"], work["is_top_1pct"]) == (0.99998271, True)
    assert work["openalex_updated_at"] == datetime(2026, 9, 22, 6, 50, 58, 402377, tzinfo=UTC)
    assert rows.source == {
        "source_id": "S137773608", "display_name": "Nature", "type": "journal",
        "issn_l": "0028-0836", "host_organization_name": "Nature Portfolio",
    }
    assert rows.relevance_score is None


def test_institutions_come_from_authorships(works_page):
    raw = by_id(works_page, "W4384071683")
    assert raw["institutions"] == []  # the OpenAlex quirk transform.py works around
    rows = transform_work(raw)
    assert rows.institutions == [{
        "institution_id": "I1291425158", "display_name": "Google (United States)",
        "ror_id": "00njsd438", "country_code": "US", "type": "company",
    }]
    assert rows.authorship_institutions == [
        {"work_id": "W4384071683", "author_seq": 0, "institution_id": "I1291425158"},
        {"work_id": "W4384071683", "author_seq": 1, "institution_id": "I1291425158"},
    ]
    assert rows.countries == ["US"]
    assert [a["is_corresponding"] for a in rows.work_authors] == [True, True]


def test_author_without_openalex_id_keeps_the_authorship(works_page):
    rows = transform_work(by_id(works_page, "W4404534210"))
    first = rows.work_authors[0]
    assert (first["author_id"], first["raw_author_name"]) == (None, "Lei Huang")
    assert [a["author_id"] for a in rows.authors] == ["A5055989750"]
    assert rows.countries == ["CN"]


def test_preprint_without_affiliations_or_references(works_page):
    rows = transform_work(by_id(works_page, "W4389984066"))
    assert rows.work["type"] == "preprint"
    assert rows.work["fwci"] is None and rows.work["citation_percentile"] is None
    assert rows.institutions == [] and rows.authorship_institutions == [] and rows.references == []
    assert rows.relevance_score == 661.9417
    assert rows.work_authors[0]["raw_author_name"] == "Gao, Yunfan"


def test_topics_keywords_references_and_yearly_citations(works_page):
    rows = transform_work(by_id(works_page, "W4384071683"))
    assert [(t["topic_id"], t["is_primary"]) for t in rows.work_topics] == [("T10028", True), ("T11636", False)]
    assert rows.topics[0] == {
        "topic_id": "T10028", "display_name": "Topic Modeling",
        "subfield_id": "1702", "subfield_name": "Artificial Intelligence",
        "field_id": "17", "field_name": "Computer Science",
        "domain_id": "3", "domain_name": "Physical Sciences",
    }
    assert [k["keyword_id"] for k in rows.keywords] == ["computer-science", "benchmark"]
    assert rows.references == ["W1981208470", "W2042492924", "W2086519542"]
    assert rows.yearly_citations[0] == {"work_id": "W4384071683", "year": 2026, "cited_by_count": 1361}


def test_duplicate_references_are_collapsed(works_page):
    raw = by_id(works_page, "W4404534210")
    raw["referenced_works"] = raw["referenced_works"] + raw["referenced_works"][:1]
    assert transform_work(raw).references == ["W89714523", "W398859631"]
