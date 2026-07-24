"""Tests for browser-uploaded folder persistence (#54): path safety + size/count limits.

Exercises ``save_uploaded_folder`` directly against a temp directory — no HTTP, no LLM — mirroring
the style of ``test_data_backend.py``'s direct backend tests.
"""

import io

import pytest
from fastapi import HTTPException, UploadFile

from src.app.core.common.config import settings
from src.app.core.sandbox.upload import save_uploaded_folder

pytestmark = pytest.mark.asyncio


def _upload(filename: str, content: bytes = b"data") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(content))


class TestSaveUploadedFolder:
    async def test_writes_files_preserving_relative_structure(self, tmp_path):
        dest = tmp_path / "dest"
        files = [_upload("a.txt", b"A"), _upload("sub/b.txt", b"B")]

        result = await save_uploaded_folder(str(dest), files)

        assert result == str(dest)
        assert (dest / "a.txt").read_bytes() == b"A"
        assert (dest / "sub" / "b.txt").read_bytes() == b"B"

    async def test_reupload_replaces_previous_content(self, tmp_path):
        dest = tmp_path / "dest"
        await save_uploaded_folder(str(dest), [_upload("old.txt", b"old")])
        assert (dest / "old.txt").exists()

        await save_uploaded_folder(str(dest), [_upload("new.txt", b"new")])

        assert not (dest / "old.txt").exists()
        assert (dest / "new.txt").read_bytes() == b"new"

    async def test_rejects_parent_traversal_filename(self, tmp_path):
        dest = tmp_path / "dest"
        with pytest.raises(HTTPException) as exc:
            await save_uploaded_folder(str(dest), [_upload("../evil.txt")])
        assert exc.value.status_code == 400
        assert not dest.exists()

    async def test_rejects_embedded_absolute_path(self, tmp_path):
        dest = tmp_path / "dest"
        with pytest.raises(HTTPException) as exc:
            await save_uploaded_folder(str(dest), [_upload("C:/evil.txt")])
        assert exc.value.status_code == 400

    async def test_rejects_empty_upload(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            await save_uploaded_folder(str(tmp_path / "dest"), [])
        assert exc.value.status_code == 400

    async def test_rejects_too_many_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_UPLOAD_MAX_FILES", 2)
        files = [_upload(f"{i}.txt") for i in range(3)]

        with pytest.raises(HTTPException) as exc:
            await save_uploaded_folder(str(tmp_path / "dest"), files)
        assert exc.value.status_code == 413

    async def test_rejects_oversized_upload(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_UPLOAD_MAX_BYTES", 10)
        dest = tmp_path / "dest"

        with pytest.raises(HTTPException) as exc:
            await save_uploaded_folder(str(dest), [_upload("big.txt", b"x" * 100)])
        assert exc.value.status_code == 413
        assert not dest.exists()

    async def test_rejects_when_sandbox_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "SANDBOX_ENABLED", False)

        with pytest.raises(HTTPException) as exc:
            await save_uploaded_folder(str(tmp_path / "dest"), [_upload("a.txt")])
        assert exc.value.status_code == 503
