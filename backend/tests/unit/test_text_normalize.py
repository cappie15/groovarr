from app.domain.text_normalize import normalize_for_comparison


def test_case_and_accent_insensitive() -> None:
    assert normalize_for_comparison("Beyoncé") == normalize_for_comparison("beyonce")


def test_punctuation_ignored() -> None:
    # Apostrophes/exclamation marks fold to whitespace consistently on both
    # sides, so differently-punctuated spellings of the same title still
    # compare equal after whitespace collapsing.
    assert normalize_for_comparison("Don't Stop Me Now!") == normalize_for_comparison("Don't Stop Me Now")


def test_whitespace_collapsed() -> None:
    assert normalize_for_comparison("  Song   Title  ") == "song title"


def test_empty_input() -> None:
    assert normalize_for_comparison("") == ""
    assert normalize_for_comparison("   ") == ""
