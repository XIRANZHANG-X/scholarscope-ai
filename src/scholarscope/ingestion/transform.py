"""Pure functions turning one OpenAlex work record into rows for the core and bridge tables.

Two OpenAlex quirks this module handles (observed 2026-09-22):
- the top-level `institutions` list can be empty while authorships carry institutions,
  so institutions are always taken from `authorships[].institutions`;
- `authorships[].author.id` can be null (author not yet disambiguated).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi:")


@dataclass(frozen=True)
class WorkRows:
    work: dict
    source: dict | None
    authors: list[dict]
    institutions: list[dict]
    countries: list[str]
    topics: list[dict]
    keywords: list[dict]
    work_authors: list[dict]
    authorship_institutions: list[dict]
    authorship_countries: list[dict]
    work_topics: list[dict]
    work_keywords: list[dict]
    references: list[str]
    yearly_citations: list[dict]
    relevance_score: float | None


def short_id(url: str | None) -> str | None:
    """'https://openalex.org/W123' -> 'W123'; 'https://openalex.org/subfields/1702' -> '1702'."""
    if not url:
        return None
    return url.rstrip("/").rsplit("/", 1)[-1]


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    value = doi.strip().lower()
    for prefix in _DOI_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value or None


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Rebuild abstract text from OpenAlex's inverted index; drops a leading 'Abstract' label."""
    if not inverted_index:
        return None
    words = [word for _, word in sorted((pos, word) for word, positions in inverted_index.items() for pos in positions)]
    if words and words[0].lower() == "abstract":
        words = words[1:]
    text = " ".join(words).strip()
    return text or None


def parse_timestamp(value: str | None) -> datetime | None:
    """OpenAlex timestamps are naive UTC ('2026-09-22T06:50:58.402377')."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _topic_row(topic: dict) -> dict:
    subfield, field, domain = (topic.get(k) or {} for k in ("subfield", "field", "domain"))
    return {
        "topic_id": short_id(topic["id"]),
        "display_name": topic["display_name"],
        "subfield_id": short_id(subfield.get("id")),
        "subfield_name": subfield.get("display_name"),
        "field_id": short_id(field.get("id")),
        "field_name": field.get("display_name"),
        "domain_id": short_id(domain.get("id")),
        "domain_name": domain.get("display_name"),
    }


def transform_work(work: dict) -> WorkRows:
    work_id = short_id(work["id"])

    src = (work.get("primary_location") or {}).get("source")
    source = None
    if src and src.get("id"):
        source = {
            "source_id": short_id(src["id"]),
            "display_name": src.get("display_name") or short_id(src["id"]),
            "type": src.get("type"),
            "issn_l": src.get("issn_l"),
            "host_organization_name": src.get("host_organization_name"),
        }

    open_access = work.get("open_access") or {}
    percentile = work.get("citation_normalized_percentile") or {}
    work_row = {
        "work_id": work_id,
        "doi": normalize_doi(work.get("doi")),
        "title": work.get("title") or work.get("display_name"),
        "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "publication_date": date.fromisoformat(work["publication_date"]),
        "publication_year": work["publication_year"],
        "type": work["type"],
        "language": work.get("language"),
        "source_id": source["source_id"] if source else None,
        "is_oa": open_access.get("is_oa"),
        "oa_status": open_access.get("oa_status"),
        "cited_by_count": work.get("cited_by_count") or 0,
        "fwci": work.get("fwci"),
        "citation_percentile": percentile.get("value"),
        "is_top_1pct": percentile.get("is_in_top_1_percent"),
        "is_top_10pct": percentile.get("is_in_top_10_percent"),
        "referenced_works_count": work.get("referenced_works_count") or 0,
        "is_retracted": bool(work.get("is_retracted")),
        "openalex_updated_at": parse_timestamp(work.get("updated_date")),
    }

    authors: dict[str, dict] = {}
    institutions: dict[str, dict] = {}
    countries: set[str] = set()
    work_authors: list[dict] = []
    authorship_institutions: dict[tuple, dict] = {}
    authorship_countries: dict[tuple, dict] = {}
    for seq, authorship in enumerate(work.get("authorships") or []):
        author = authorship.get("author") or {}
        author_id = short_id(author.get("id"))
        if author_id:
            authors[author_id] = {
                "author_id": author_id,
                "display_name": author.get("display_name") or authorship.get("raw_author_name") or author_id,
                "orcid": short_id(author.get("orcid")),
            }
        work_authors.append({
            "work_id": work_id,
            "author_seq": seq,
            "author_id": author_id,
            "raw_author_name": authorship.get("raw_author_name") or author.get("display_name") or "",
            "author_position": authorship.get("author_position"),
            "is_corresponding": bool(authorship.get("is_corresponding")),
            "raw_affiliation_strings": list(authorship.get("raw_affiliation_strings") or []),
        })
        for inst in authorship.get("institutions") or []:
            institution_id = short_id(inst.get("id"))
            if not institution_id:
                continue
            country = (inst.get("country_code") or "").upper() or None
            if country:
                countries.add(country)
            institutions[institution_id] = {
                "institution_id": institution_id,
                "display_name": inst.get("display_name") or institution_id,
                "ror_id": short_id(inst.get("ror")),
                "country_code": country,
                "type": inst.get("type"),
            }
            authorship_institutions[(seq, institution_id)] = {
                "work_id": work_id, "author_seq": seq, "institution_id": institution_id,
            }
        for code in authorship.get("countries") or []:
            country = code.upper()
            countries.add(country)
            authorship_countries[(seq, country)] = {"work_id": work_id, "author_seq": seq, "country_code": country}

    primary_topic = work.get("primary_topic") or {}
    primary_topic_id = short_id(primary_topic.get("id"))
    topic_list = list(work.get("topics") or [])
    if primary_topic_id and primary_topic_id not in {short_id(t["id"]) for t in topic_list}:
        topic_list.append(primary_topic)
    topics = [_topic_row(t) for t in topic_list]
    work_topics = [
        {"work_id": work_id, "topic_id": short_id(t["id"]), "score": t.get("score") or 0.0,
         "is_primary": short_id(t["id"]) == primary_topic_id}
        for t in topic_list
    ]

    keyword_list = work.get("keywords") or []
    keywords = [{"keyword_id": short_id(k["id"]), "display_name": k["display_name"]} for k in keyword_list]
    work_keywords = [
        {"work_id": work_id, "keyword_id": short_id(k["id"]), "score": k.get("score") or 0.0} for k in keyword_list
    ]

    references = list(dict.fromkeys(ref for ref in map(short_id, work.get("referenced_works") or []) if ref))
    yearly_citations = [
        {"work_id": work_id, "year": c["year"], "cited_by_count": c["cited_by_count"]}
        for c in work.get("counts_by_year") or []
    ]

    return WorkRows(
        work=work_row,
        source=source,
        authors=list(authors.values()),
        institutions=list(institutions.values()),
        countries=sorted(countries),
        topics=topics,
        keywords=keywords,
        work_authors=work_authors,
        authorship_institutions=list(authorship_institutions.values()),
        authorship_countries=list(authorship_countries.values()),
        work_topics=work_topics,
        work_keywords=work_keywords,
        references=references,
        yearly_citations=yearly_citations,
        relevance_score=work.get("relevance_score"),
    )
