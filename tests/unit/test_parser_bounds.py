import pytest

from testmind.infrastructure.knowledge.document_parser import parse_document


def test_parser_rejects_oversized_input_before_decode():
    with pytest.raises(ValueError, match="size"):
        parse_document(b"abcdef", "text/plain", filename="a.txt", max_bytes=5)


def test_parser_rejects_excessive_extracted_text():
    with pytest.raises(ValueError, match="text"):
        parse_document(b"abcdef", "text/plain", filename="a.txt", max_text_chars=5)


async def test_parser_deadline_terminates_child():
    from testmind.infrastructure.knowledge.document_parser import parse_document_bounded

    with pytest.raises(TimeoutError):
        await parse_document_bounded(b"text", "text/plain", filename="a.txt", timeout_seconds=.001)
