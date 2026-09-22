import gzip
import json

from scholarscope.ingestion.raw_cache import RawCache


def test_write_page_round_trips_gzipped_json(tmp_path):
    cache = RawCache(tmp_path)
    path = cache.write_page(7, "rag.retrieval_augmented_generation", 3, {"results": [{"title": "检索增强生成"}]})

    assert path == tmp_path / "openalex" / "run-00007" / "rag.retrieval_augmented_generation" / "page-00003.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        assert json.load(fh)["results"][0]["title"] == "检索增强生成"
    assert list(path.parent.glob("*.tmp")) == []


def test_rewriting_a_page_replaces_it(tmp_path):
    cache = RawCache(tmp_path)
    cache.write_page(1, "q", 1, {"version": 1})
    path = cache.write_page(1, "q", 1, {"version": 2})
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        assert json.load(fh) == {"version": 2}
