"""Deterministic quality and index-acceptance checks."""

from __future__ import annotations

from testmind.infrastructure.knowledge.chunking import KnowledgeChunkInput
from testmind.infrastructure.knowledge.document_parser import ParsedDocument
from testmind.models.knowledge import QualityCheck, QualityReport, quality_report


def evaluate_quality(
    parsed: ParsedDocument,
    chunks: list[KnowledgeChunkInput],
    *,
    vector_count: int | None = None,
    min_chunks: int = 1,
    max_chunks: int = 5000,
    max_parse_ms: int = 60_000,
    max_text_chars: int = 5_000_000,
) -> QualityReport:
    text = parsed.text
    checks = [
        QualityCheck(
            name="extracted_non_empty",
            status="passed" if text.strip() else "failed",
            severity="error",
            details={"characters": len(text)},
        ),
        QualityCheck(
            name="chunk_count_range",
            status="passed" if min_chunks <= len(chunks) <= max_chunks else "failed",
            severity="error",
            details={"count": len(chunks), "min": min_chunks, "max": max_chunks},
        ),
        QualityCheck(
            name="chunk_content_and_locator",
            status="passed"
            if all(chunk.content.strip() and chunk.source_locator for chunk in chunks)
            else "failed",
            severity="error",
        ),
        QualityCheck(
            name="parse_duration",
            status="passed" if parsed.parse_duration_ms <= max_parse_ms else "failed",
            severity="warning",
            details={"milliseconds": parsed.parse_duration_ms},
        ),
        QualityCheck(
            name="text_length",
            status="passed" if len(text) <= max_text_chars else "failed",
            severity="error",
            details={"characters": len(text), "max": max_text_chars},
        ),
        QualityCheck(
            name="garbled_text_ratio",
            status="passed" if _replacement_ratio(text) <= 0.02 else "failed",
            severity="warning",
            details={"ratio": _replacement_ratio(text)},
        ),
    ]
    if vector_count is not None:
        checks.append(
            QualityCheck(
                name="vector_count_match",
                status="passed" if vector_count == len(chunks) else "failed",
                severity="error",
                details={"expected": len(chunks), "actual": vector_count},
            )
        )
    for warning in parsed.warnings:
        checks.append(QualityCheck(name=warning, status="failed", severity="warning"))
    report = quality_report(checks, metrics={"text_chars": len(text), "chunk_count": len(chunks)})
    return report


def record_index_acceptance(
    report: QualityReport, *, expected: int, actual: int
) -> QualityReport:
    passed = expected > 0 and expected == actual
    check = QualityCheck(
        name="vector_count_match",
        status="passed" if passed else "failed",
        severity="error",
        details={"expected": expected, "actual": actual},
    )
    checks = tuple(item for item in report.checks if item.name != check.name) + (check,)
    return report.model_copy(
        update={"index_status": "accepted" if passed else "failed", "checks": checks}
    )


def _replacement_ratio(text: str) -> float:
    return text.count("\ufffd") / max(1, len(text))
