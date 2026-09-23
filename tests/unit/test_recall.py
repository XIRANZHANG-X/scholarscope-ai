from datetime import date

import pytest

from scholarscope.db import PROJECT_ROOT
from scholarscope.ingestion.recall import CS_FIELD_ID, THEMES, RecallConfig, build_filter, load_recall_config

TO_DATE = date(2026, 9, 22)


def _config(**overrides) -> RecallConfig:
    data = {
        "from_date": "2019-01-01",
        "types": ["article", "preprint"],
        "primary_field_ids": [],
        "queries": [{"key": "rag.rag", "theme": "rag", "phrase": "retrieval augmented generation"}],
    }
    data.update(overrides)
    return RecallConfig.from_dict(data)


def test_build_filter_combines_phrase_dates_and_types():
    config = _config()
    assert build_filter(config, config.queries[0], to_date=TO_DATE) == (
        'title_and_abstract.search:"retrieval augmented generation",'
        "from_publication_date:2019-01-01,to_publication_date:2026-09-22,type:article|preprint"
    )


def test_build_filter_field_scope_and_type_toggle():
    config = _config(primary_field_ids=["17"])
    query = config.queries[0]
    assert build_filter(config, query, to_date=TO_DATE).endswith(",primary_topic.field.id:17")
    unrestricted = build_filter(config, query, to_date=TO_DATE, field_ids=(), include_types=False)
    assert "primary_topic" not in unrestricted
    assert "type:" not in unrestricted


@pytest.mark.parametrize("phrase", ["a, b", 'say "hi"', "a|b", "   "])
def test_rejects_phrases_that_break_filter_syntax(phrase):
    with pytest.raises(ValueError, match="phrase"):
        _config(queries=[{"key": "x", "theme": "rag", "phrase": phrase}])


def test_rejects_unknown_theme_and_duplicate_keys():
    with pytest.raises(ValueError, match="theme"):
        _config(queries=[{"key": "x", "theme": "robotics", "phrase": "robot"}])
    query = {"key": "x", "theme": "rag", "phrase": "rag"}
    with pytest.raises(ValueError, match="duplicate"):
        _config(queries=[query, query])


def test_round_trips_through_dict():
    config = _config()
    assert RecallConfig.from_dict(config.to_dict()) == config


def test_downloaded_corpus_is_rag_in_computer_science():
    config = load_recall_config(PROJECT_ROOT / "config" / "recall.toml")
    assert [q.key for q in config.queries] == ["rag.retrieval_augmented_generation"]
    assert config.primary_field_ids == (CS_FIELD_ID,)
    assert "conference-paper" in config.types


def test_context_phrases_cover_all_four_themes():
    config = load_recall_config(PROJECT_ROOT / "config" / "context.toml")
    assert {q.theme for q in config.queries} == set(THEMES)
