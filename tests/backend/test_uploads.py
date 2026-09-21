"""Receiving uploaded files.

The claims this module makes in its docstring - that the client's filename is
never used as a path, and that the size limit is enforced on bytes rather than
on a header - are only worth making if they are checked.
"""

from __future__ import annotations

import io

import pytest

from app.errors import PayloadTooLargeError, UnsupportedFileError
from app.services.uploads import (
    discard_orphaned_uploads,
    extension_of,
    save_upload,
    temporary_upload,
)


class FakeUpload:
    """The two attributes `save_upload` uses, and nothing else."""

    def __init__(self, filename: str | None, content: bytes) -> None:
        self.filename = filename
        self.file = io.BytesIO(content)


ALLOWED = {".kml", ".kmz", ".geojson"}


class TestExtensionHandling:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("stand.kml", ".kml"),
            ("STAND.KML", ".kml"),
            ("archive.tar.kmz", ".kmz"),
            ("no-extension", ""),
            (None, ""),
        ],
    )
    def test_the_extension_is_read_from_the_name(self, filename, expected):
        assert extension_of(filename) == expected

    @pytest.mark.parametrize(
        "filename",
        ["../../../etc/passwd.kml", r"..\..\windows\system32\evil.kml", "/tmp/absolute.kml"],
    )
    def test_directory_components_are_discarded(self, filename):
        # Only the extension survives; the path the client suggested is never
        # consulted for anything.
        assert extension_of(filename) == ".kml"


class TestSaving:
    def test_the_stored_name_is_generated_not_taken_from_the_client(self, tmp_path):
        upload = FakeUpload("../../escape.kml", b"<kml/>")
        path = save_upload(upload, tmp_path, ALLOWED, 1024)
        assert path.parent == tmp_path
        assert "escape" not in path.name
        assert path.suffix == ".kml"

    def test_the_bytes_arrive_intact(self, tmp_path):
        upload = FakeUpload("stand.kml", b"<kml>hello</kml>")
        path = save_upload(upload, tmp_path, ALLOWED, 1024)
        assert path.read_bytes() == b"<kml>hello</kml>"

    def test_an_unsupported_extension_is_refused_before_anything_is_written(self, tmp_path):
        with pytest.raises(UnsupportedFileError):
            save_upload(FakeUpload("boundary.shp", b"x" * 100), tmp_path, ALLOWED, 1024)
        assert list(tmp_path.glob("*")) == []

    def test_an_oversized_stream_is_abandoned_and_cleaned_up(self, tmp_path):
        # The limit applies to bytes actually read, so a client that lies about
        # its size still cannot fill the disk before being rejected.
        with pytest.raises(PayloadTooLargeError):
            save_upload(FakeUpload("stand.kml", b"x" * 5000), tmp_path, ALLOWED, 1024)
        assert list(tmp_path.glob("*")) == []

    def test_a_file_exactly_at_the_limit_is_accepted(self, tmp_path):
        path = save_upload(FakeUpload("stand.kml", b"x" * 1024), tmp_path, ALLOWED, 1024)
        assert path.stat().st_size == 1024

    def test_an_empty_upload_is_stored_for_the_parser_to_explain(self, tmp_path):
        # "This KML is empty" is a better message than "unsupported file", and
        # only the parser is in a position to say it.
        path = save_upload(FakeUpload("stand.kml", b""), tmp_path, ALLOWED, 1024)
        assert path.exists() and path.stat().st_size == 0

    def test_the_destination_directory_is_created_if_absent(self, tmp_path):
        target = tmp_path / "nested" / "uploads"
        path = save_upload(FakeUpload("stand.kml", b"<kml/>"), target, ALLOWED, 1024)
        assert path.parent == target


class TestTemporaryUpload:
    def test_the_file_exists_inside_the_block_and_not_after(self, tmp_path):
        with temporary_upload(FakeUpload("stand.kml", b"<kml/>"), tmp_path, ALLOWED, 1024) as path:
            assert path.exists()
        assert not path.exists()

    def test_it_is_removed_even_when_the_body_raises(self, tmp_path):
        captured = None
        with pytest.raises(ValueError):
            with temporary_upload(
                FakeUpload("stand.kml", b"<kml/>"), tmp_path, ALLOWED, 1024
            ) as path:
                captured = path
                raise ValueError("parsing failed")
        assert captured is not None and not captured.exists()


class TestOrphanedUploadSweep:
    """A file left behind by a killed run belongs to a job that no longer exists."""

    def test_files_left_from_a_previous_run_are_removed(self, tmp_path):
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        (upload_dir / "abc123.tif").write_bytes(b"leftover imagery")
        (upload_dir / "def456.kml").write_text("<kml/>", encoding="utf-8")

        removed = discard_orphaned_uploads(upload_dir)

        assert removed == 2
        assert list(upload_dir.iterdir()) == []

    def test_an_empty_or_absent_directory_is_not_an_error(self, tmp_path):
        assert discard_orphaned_uploads(tmp_path / "never-created") == 0
        (tmp_path / "empty").mkdir()
        assert discard_orphaned_uploads(tmp_path / "empty") == 0
