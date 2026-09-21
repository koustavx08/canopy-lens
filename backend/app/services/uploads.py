"""Receiving uploaded files safely.

Two things matter here and neither is about geospatial work.

**The size limit is enforced while reading, not after.** A `Content-Length`
header is a claim by the client, and checking it is not the same as checking
the bytes. The stream is read in chunks and abandoned the moment it exceeds the
limit, so an oversized or lying upload cannot fill the disk first and be
rejected second.

**The filename from the client is never used as a path.** Only its extension is
trusted enough to keep, and only after being matched against an allow-list; the
stored name is generated here. `../../etc/passwd` arriving as a filename is a
well-worn trick and it is defused by not using the value at all.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from ..errors import PayloadTooLargeError, UnsupportedFileError

log = logging.getLogger(__name__)

CHUNK_BYTES = 1024 * 1024


class UploadLike(Protocol):
    """The part of Starlette's UploadFile this module uses.

    Typed structurally so the helper can be tested with a plain object and does
    not drag a web framework into the service layer.
    """

    filename: str | None

    @property
    def file(self): ...


def extension_of(filename: str | None) -> str:
    """The lowercase extension of an uploaded filename, path components ignored."""
    if not filename:
        return ""
    name = Path(str(filename).replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    return suffix


def save_upload(
    upload: UploadLike,
    destination_dir: Path,
    allowed_extensions: set[str],
    max_bytes: int,
    *,
    kind: str = "file",
) -> Path:
    """Stream an upload to disk, returning the path it was written to.

    Raises:
        UnsupportedFileError: the extension is not in `allowed_extensions`.
        PayloadTooLargeError: the stream exceeded `max_bytes`.
    """
    suffix = extension_of(upload.filename)
    if suffix not in allowed_extensions:
        raise UnsupportedFileError(
            f"{suffix or 'A file with no extension'} is not a supported {kind} "
            f"format. Supported: {', '.join(sorted(allowed_extensions))}.",
            detail={"supported": sorted(allowed_extensions), "received": suffix},
        )

    destination_dir.mkdir(parents=True, exist_ok=True)
    path = destination_dir / f"{uuid.uuid4().hex}{suffix}"

    written = 0
    source = upload.file
    try:
        with path.open("wb") as target:
            while True:
                chunk = source.read(CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise PayloadTooLargeError(
                        f"The uploaded {kind} is larger than the "
                        f"{max_bytes / (1024 * 1024):.0f} MB limit for this server.",
                        detail={"limit_bytes": max_bytes},
                    )
                target.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    if written == 0:
        # Let the parser produce the message: it knows what an empty KML means
        # in a way this function does not.
        log.info("Received an empty %s upload (%s)", kind, upload.filename)

    log.info("Stored %s upload as %s (%d bytes)", kind, path.name, written)
    return path


@contextmanager
def temporary_upload(
    upload: UploadLike,
    destination_dir: Path,
    allowed_extensions: set[str],
    max_bytes: int,
    *,
    kind: str = "file",
) -> Iterator[Path]:
    """`save_upload` for work that finishes within the request.

    A boundary preview is read, measured and answered in one call; keeping the
    file afterwards would accumulate uploads nobody will ever ask for again.
    """
    path = save_upload(upload, destination_dir, allowed_extensions, max_bytes, kind=kind)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def discard_orphaned_uploads(upload_dir: Path) -> int:
    """Delete every file left in the upload directory, returning how many.

    Called once at startup. An analysis removes its own upload as it finishes,
    so a file surviving a restart is one whose job no longer exists: the job
    store lives in the process and does not outlive it either. Nothing here can
    be resumed, so nothing here is worth keeping.
    """
    if not upload_dir.is_dir():
        return 0
    removed = 0
    for entry in upload_dir.iterdir():
        try:
            if entry.is_file():
                entry.unlink()
            else:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError as error:
            # A file another process still holds open is not worth failing
            # startup over; it will be swept on the next restart.
            log.warning("Could not remove orphaned upload %s: %s", entry.name, error)
            continue
        removed += 1
    return removed


def clear_directory(path: Path) -> None:
    """Remove a directory and everything in it, ignoring what is not there."""
    shutil.rmtree(path, ignore_errors=True)
