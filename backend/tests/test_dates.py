from datetime import UTC, datetime

from app.ingestion.dates import document_date


def d(y, m, day):
    return datetime(y, m, day, tzinfo=UTC)


def test_labelled_header_dates_prefer_effective():
    md = "# Retention standard (v2)\n\n**Version:** 2.0 · **Effective:** September 15, 2026\n"
    assert document_date(md) == d(2026, 9, 15)
    assert document_date("# Memo\n\n**Date:** October 19, 2026\n") == d(2026, 10, 19)
    md = "# Memo\n\n**Date:** October 19, 2026\n**Approved:** Oct 2, 2026\n"
    assert document_date(md) == d(2026, 10, 2)  # an approval date outranks a bare "date"
    md = "# Policy\n\n**Approved:** 2026-09-12 · **Effective:** 2026-10-01\n"
    assert document_date(md) == d(2026, 10, 1)
    assert document_date("# Notes\n\nLast updated: 3 March 2026\n") == d(2026, 3, 3)


def test_title_date_is_a_fallback():
    md = "# Annual leave policy (effective October 1, 2026)\n\nBody text."
    assert document_date(md, "Annual leave policy (effective October 1, 2026)") == d(2026, 10, 1)
    assert document_date("# Q3 board update (October 2026)\n") == d(2026, 10, 1)


def test_dates_in_tables_and_body_are_ignored():
    md = (
        "# Vendor record: Cobaltline\n\n"
        "| Field | Value |\n|---|---|\n"
        "| Renewal date | March 31, 2027 |\n"
        "| BAA | Executed April 1, 2023; updated October 2024 |\n\n"
        "The contract renews on March 31, 2027."
    )
    assert document_date(md) is None
    assert document_date("# Runbook\n\nShifts start Tuesday. Pay rises from January 2027.") is None


def test_invalid_dates_do_not_crash():
    assert document_date("# X\n\nDate: February 31, 2026\n") is None
    assert document_date("") is None
