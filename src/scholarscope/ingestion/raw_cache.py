"""Gzipped copies of raw API pages, so a schema change can be re-transformed without re-spending API budget."""

import gzip
import json
from pathlib import Path


class RawCache:
    def __init__(self, root: Path) -> None:
        self._root = root

    def page_path(self, run_id: int, query_key: str, page_no: int) -> Path:
        return self._root / "openalex" / f"run-{run_id:05d}" / query_key / f"page-{page_no:05d}.json.gz"

    def write_page(self, run_id: int, query_key: str, page_no: int, payload: dict) -> Path:
        path = self.page_path(run_id, query_key, page_no)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        tmp.replace(path)
        return path
