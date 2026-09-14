"""Generic text normalization for deterministic comparison.

Used by the existing-library scanner (Phase 3) to compare a scanned file's
tags/filename against known `Track` rows, and intended to be reused by the
YouTube candidate-scoring engine (Phase 4) for the same kind of
artist/title comparison — see docs/00-research-and-architecture-review.md §8.

This is a *comparison* normalizer, not a display formatter: it strips
diacritics/punctuation/case so "Beyoncé" and "beyonce" compare equal, but it
is never used to decide what to store or display (that stays the original,
unmangled Unicode — see app/domain/naming.py, which sanitizes for filesystem
safety but does not case-fold or strip accents).
"""

import re
import unicodedata

_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_comparison(text: str) -> str:
    """Fold `text` down to a lowercase, accent-stripped, punctuation-free,
    whitespace-collapsed form suitable for equality/containment comparison.

    Empty/whitespace-only input normalizes to "".
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = without_accents.casefold()
    alnum_only = _NON_ALNUM_RE.sub(" ", folded)
    return _WHITESPACE_RE.sub(" ", alnum_only).strip()
