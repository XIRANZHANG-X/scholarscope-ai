"""Candidate-recall queries (architecture §5.2, stage 1) and the OpenAlex filters built from them."""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

THEMES = ("foundation_models", "llm", "rag", "agents")
CS_FIELD_ID = "17"  # OpenAlex field "Computer Science"
_FORBIDDEN_IN_PHRASE = (",", '"', "|")


@dataclass(frozen=True)
class RecallQuery:
    key: str
    theme: str
    phrase: str


@dataclass(frozen=True)
class RecallConfig:
    from_date: date
    types: tuple[str, ...]
    primary_field_ids: tuple[str, ...]
    queries: tuple[RecallQuery, ...]

    @classmethod
    def from_dict(cls, data: dict) -> RecallConfig:
        queries = tuple(RecallQuery(q["key"], q["theme"], q["phrase"]) for q in data["queries"])
        keys = [q.key for q in queries]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate recall query keys: {sorted(k for k in set(keys) if keys.count(k) > 1)}")
        for q in queries:
            if q.theme not in THEMES:
                raise ValueError(f"query {q.key!r}: unknown theme {q.theme!r}; expected one of {THEMES}")
            if not q.phrase.strip() or any(ch in q.phrase for ch in _FORBIDDEN_IN_PHRASE):
                raise ValueError(f"query {q.key!r}: phrase must be non-empty without , \" or |")
        return cls(
            from_date=date.fromisoformat(str(data["from_date"])),
            types=tuple(data.get("types", ())),
            primary_field_ids=tuple(str(f) for f in data.get("primary_field_ids", ())),
            queries=queries,
        )

    def to_dict(self) -> dict:
        return {
            "from_date": self.from_date.isoformat(),
            "types": list(self.types),
            "primary_field_ids": list(self.primary_field_ids),
            "queries": [{"key": q.key, "theme": q.theme, "phrase": q.phrase} for q in self.queries],
        }


def load_recall_config(path: Path) -> RecallConfig:
    with path.open("rb") as fh:
        return RecallConfig.from_dict(tomllib.load(fh))


def build_filter(
    config: RecallConfig,
    query: RecallQuery,
    *,
    to_date: date,
    field_ids: Sequence[str] | None = None,
    include_types: bool = True,
) -> str:
    """OpenAlex `filter` value for one recall query.

    `field_ids=None` uses the configured primary-topic fields; pass `()` to search all fields.
    """
    parts = [
        f'title_and_abstract.search:"{query.phrase}"',
        f"from_publication_date:{config.from_date.isoformat()}",
        f"to_publication_date:{to_date.isoformat()}",
    ]
    if include_types and config.types:
        parts.append("type:" + "|".join(config.types))
    ids = config.primary_field_ids if field_ids is None else tuple(field_ids)
    if ids:
        parts.append("primary_topic.field.id:" + "|".join(ids))
    return ",".join(parts)
