"""Shared atomic same-filesystem move, with a verified cross-filesystem
fallback. Used by both the explicit Organize/Rename action (Phase 3,
app/services/organize.py) and the acquisition pipeline's atomic import
(Phase 5, app/services/acquisition.py, §52) — one implementation of this
destructive-adjacent operation, not two (§70: a move/delete routine deserves
defense in depth, not duplicated logic that could drift apart).
"""

import errno
import os
import shutil
from pathlib import Path


class AtomicMoveError(Exception):
    """The move could not be completed safely; the source file is left
    untouched on disk."""


def atomic_move(source: Path, dest: Path) -> None:
    """Move `source` to `dest`. Same-filesystem case is a plain atomic
    `rename`. Cross-filesystem case copies then verifies the byte size
    matches before removing the source — if verification fails, the partial
    destination is removed and the original file is left exactly as it was.
    """
    try:
        os.rename(source, dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copy2(source, dest)
        if dest.stat().st_size != source.stat().st_size:
            dest.unlink(missing_ok=True)
            raise AtomicMoveError(
                "Cross-filesystem copy verification failed; original file left untouched."
            ) from exc
        source.unlink()
