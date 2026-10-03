"""Load and validate the labelled corpora.

Validation is strict on purpose. A corpus is a measuring instrument: a duplicate
id silently overwrites a case, and an unlabelled rationale is a guess that will
still show up in the numbers as if it were evidence.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from continuum.evaluation.types import ExtractionCase, ResolutionCase, RetrievalCase

CORPUS_DIR = Path(__file__).parent / "corpus"
RESOLUTION_CORPUS = CORPUS_DIR / "resolution.yaml"
EXTRACTION_CORPUS = CORPUS_DIR / "extraction.yaml"


class CorpusError(ValueError):
    pass


def load_resolution_cases(path: Path | None = None) -> list[ResolutionCase]:
    cases = [ResolutionCase.model_validate(row) for row in _read(path or RESOLUTION_CORPUS)]
    _check_unique(case.id for case in cases)
    for case in cases:
        if not case.why.strip():
            raise CorpusError(
                f"Case {case.id!r} has no rationale. A label without a why is a guess."
            )
    return cases


def load_resolution_cases_with(extra: list[Path]) -> list[ResolutionCase]:
    """The hand-written corpus plus cases from other files — typically the
    decisions exported from real use (`GET /feedback/export`). Ids must be unique
    across all of them: a shadowed case silently leaves the denominator."""
    cases = load_resolution_cases()
    for path in extra:
        cases.extend(load_resolution_cases(path))
    _check_unique(case.id for case in cases)
    return cases


def load_extraction_cases(path: Path | None = None) -> list[ExtractionCase]:
    cases = [ExtractionCase.model_validate(row) for row in _read(path or EXTRACTION_CORPUS)]
    _check_unique(case.id for case in cases)
    return cases


def load_extraction_cases_with(extra: list[Path]) -> list[ExtractionCase]:
    cases = load_extraction_cases()
    for path in extra:
        cases.extend(ExtractionCase.model_validate(row) for row in _read(path))
    _check_unique(case.id for case in cases)
    return cases


def load_retrieval_cases(path: Path) -> list[RetrievalCase]:
    cases = [RetrievalCase.model_validate(row) for row in _read(path)]
    _check_unique(case.id for case in cases)
    return cases


def _read(path: Path) -> list[dict]:
    if not path.exists():
        raise CorpusError(f"No corpus at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise CorpusError(f"{path.name} must be a list of cases, got {type(data).__name__}")
    return data


def _check_unique(ids) -> None:
    seen: set[str] = set()
    for case_id in ids:
        if case_id in seen:
            raise CorpusError(f"Duplicate case id {case_id!r} — one case is shadowing another.")
        seen.add(case_id)
