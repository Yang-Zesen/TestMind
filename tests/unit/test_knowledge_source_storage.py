from io import BytesIO

import pytest

from testmind.infrastructure.knowledge.source_storage import FilesystemSourceStorage


def test_storage_uses_generated_keys_and_atomic_private_file(tmp_path):
    storage = FilesystemSourceStorage(tmp_path, max_bytes=20)
    stored = storage.save(BytesIO(b"payload"), suffix=".txt")

    assert stored.key != "payload.txt"
    assert stored.size_bytes == 7
    assert len(stored.sha256) == 64
    assert storage.open(stored.key).read() == b"payload"
    storage.delete(stored.key)
    with pytest.raises(FileNotFoundError):
        storage.open(stored.key)


def test_storage_rejects_path_traversal_and_oversize_upload(tmp_path):
    storage = FilesystemSourceStorage(tmp_path, max_bytes=3)
    with pytest.raises(ValueError):
        storage.save(BytesIO(b"four"), suffix=".txt")
    with pytest.raises(ValueError):
        storage.open("../secret")
