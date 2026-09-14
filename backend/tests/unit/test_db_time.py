from datetime import UTC, datetime

from app.core.db_time import as_aware_utc


def test_naive_datetime_gets_utc_attached():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    result = as_aware_utc(naive)
    assert result.tzinfo is UTC
    assert result.replace(tzinfo=None) == naive


def test_already_aware_datetime_is_returned_unchanged():
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert as_aware_utc(aware) is aware


def test_none_stays_none():
    assert as_aware_utc(None) is None
