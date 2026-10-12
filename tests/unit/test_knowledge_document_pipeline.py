import pytest

from testmind.infrastructure.knowledge.chunking import split_document
from testmind.infrastructure.knowledge.document_parser import parse_document
from testmind.infrastructure.knowledge.document_parser import validate_upload
from testmind.infrastructure.knowledge.quality import evaluate_quality, record_index_acceptance
from testmind.models.knowledge import VersionStatus


def test_parser_accepts_utf8_markdown_and_rejects_mime_mismatch():
    parsed = parse_document(b"# Heading\n\nBody", "text/markdown", filename="doc.md")
    assert parsed.text.startswith("# Heading")
    assert parsed.page_offsets == (0,)

    with pytest.raises(ValueError):
        parse_document(b"not a pdf", "application/pdf", filename="doc.pdf")


def test_parser_rejects_invalid_utf8_without_silent_replacement():
    with pytest.raises(ValueError, match="UTF-8"):
        parse_document(b"\xff\xfe", "text/plain", filename="bad.txt")


def test_upload_validation_rejects_pdf_disguised_as_text():
    with pytest.raises(ValueError, match="signature"):
        validate_upload(b"%PDF-1.7", "text/plain", filename="doc.txt")


def test_stable_chunks_quality_and_index_acceptance_are_separate():
    parsed = parse_document(("alpha " * 80).encode(), "text/plain", filename="doc.txt")
    chunks = split_document(parsed, version_id="version-1", max_chars=100, overlap_chars=10)
    report = evaluate_quality(parsed, chunks)

    assert chunks
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert all(not chunk.is_active for chunk in chunks)
    assert report.quality_status == "passed"
    assert report.index_status == "pending"
    assert record_index_acceptance(report, expected=len(chunks), actual=len(chunks)).index_status == "accepted"
    assert record_index_acceptance(report, expected=len(chunks), actual=0).index_status == "failed"
    assert VersionStatus.INDEX_PENDING != VersionStatus.READY
