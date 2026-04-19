"""
test_phase_b.py — Validate Phase B logic against real XDRX800 XML data.

Run from project root:
    python -m pytest tasks/to_status_check/test_phase_b.py -v
    # or standalone:
    python tasks/to_status_check/test_phase_b.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running standalone
TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from clients.m3_h5_client import parse_xdrx800_xml, COLUMN_MAP


def test_parse_real_xml():
    """Parse the actual XDRX800 XML response captured from M3."""
    mock_file = TASK_DIR / "mock_data" / "to_all.xml"
    if not mock_file.exists():
        print(f"SKIP: {mock_file} not found (run capture_m3.py first)")
        return

    xml_text = mock_file.read_text(encoding="utf-8")
    rows = parse_xdrx800_xml(xml_text)

    assert len(rows) == 4, f"Expected 4 rows, got {len(rows)}"

    # Validate first row (TO 147715)
    r1 = rows[0]
    assert r1["to_number"] == "147715"
    assert r1["status_code"] == "44"
    assert r1["status_description"] == "Shipped from sending site"
    assert r1["sending_site"] == "AP-SG-MF"
    assert r1["receiving_site"] == "EU-DE-MH"
    assert r1["responsible"] == "TMOGHANAN"
    assert r1["receiver"] == "GESCHAEFER"
    assert r1["creation_date"] == "2026-04-16"
    assert r1["delivery_service"] == "Express"
    assert r1["matter_of_delivery"] == "ExpressOPS"
    print(f"  ✓ TO 147715: {r1['status']}")

    # Validate second row (TO 147297)
    r2 = rows[1]
    assert r2["to_number"] == "147297"
    assert r2["status_code"] == "54"
    assert r2["sending_site"] == "AP-SG-MF"
    print(f"  ✓ TO 147297: {r2['status']}")

    # Validate row with empty fields (TO 146271 — no arrived_at_logistics)
    r3 = rows[2]
    assert r3["to_number"] == "146271"
    assert r3["arrived_at_logistics"] == ""
    print(f"  ✓ TO 146271: arrived_at_logistics correctly empty")

    print(f"\nAll {len(rows)} rows parsed correctly.")


def test_parse_empty_xml():
    """Empty or non-data XML returns empty list."""
    assert parse_xdrx800_xml("") == []
    assert parse_xdrx800_xml("<Root><Panels></Panels></Root>") == []
    assert parse_xdrx800_xml("not xml at all") == []
    print("  ✓ Empty/non-data XML handled correctly")


def test_parse_single_row():
    """Parse a minimal XML with one LR element."""
    xml = """<?xml version="1.0"?>
    <Root><Panels><Panel><Objs><List><LView><LRows>
      <LR name="R1">
        <LC name="R1C0">999999</LC>
        <LC name="R1C3">20 - TO note printed</LC>
        <LC name="R1C6">AP-SG-MF</LC>
        <LC name="R1C8">EU-DE-MH</LC>
      </LR>
    </LRows></LView></List></Objs></Panel></Panels></Root>"""

    rows = parse_xdrx800_xml(xml)
    assert len(rows) == 1
    assert rows[0]["to_number"] == "999999"
    assert rows[0]["status_code"] == "20"
    assert rows[0]["status_description"] == "TO note printed"
    assert rows[0]["sending_site"] == "AP-SG-MF"
    assert rows[0]["receiving_site"] == "EU-DE-MH"
    print("  ✓ Single row parsed correctly")


def test_column_map_completeness():
    """All 20 columns (C0-C19) are mapped."""
    assert len(COLUMN_MAP) == 20
    assert set(COLUMN_MAP.keys()) == set(range(20))
    print(f"  ✓ Column map covers all 20 columns")


def test_enrichment():
    """Test enrich_rows_with_to_status merges M3 data correctly."""
    from tasks.to_status_check.logic import (
        build_container_row,
        enrich_rows_with_to_status,
    )

    # Simulate a Phase A row with a TO number
    fake_issue = {
        "key": "NPIOTHER-4371",
        "fields": {
            "summary": "Test container",
            "status": {"name": "In Progress"},
            "comment": {
                "comments": [
                    {"body": "TO: 147715", "created": "2026-04-16T10:00:00.000+0800"}
                ]
            },
        },
    }
    rows = [build_container_row(fake_issue)]
    assert rows[0]["to_number"] == "147715"
    assert rows[0]["to_status"] is None  # Not enriched yet

    # Simulate M3 lookup result
    to_statuses = {
        "147715": {
            "status": "44 - Shipped from sending site",
            "status_code": "44",
            "sending_site": "AP-SG-MF",
            "receiving_site": "EU-DE-MH",
            "receiver": "GESCHAEFER",
            "creation_date": "2026-04-16",
            "arrived_at_logistics": "2026-04-17",
        }
    }

    enrich_rows_with_to_status(rows, to_statuses)
    assert rows[0]["to_status"] == "44 - Shipped from sending site"
    assert rows[0]["to_status_code"] == "44"
    assert rows[0]["to_sending_site"] == "AP-SG-MF"
    assert rows[0]["to_receiving_site"] == "EU-DE-MH"
    assert rows[0]["to_arrived_date"] == "2026-04-17"
    print("  ✓ Enrichment merges M3 data correctly")


def test_enrichment_missing_to():
    """Rows without a TO number are unaffected by enrichment."""
    from tasks.to_status_check.logic import (
        build_container_row,
        enrich_rows_with_to_status,
    )

    fake_issue = {
        "key": "NPIOTHER-9999",
        "fields": {
            "summary": "No TO container",
            "status": {"name": "Open"},
            "comment": {"comments": []},
        },
    }
    rows = [build_container_row(fake_issue)]
    enrich_rows_with_to_status(rows, {"147715": {"status": "44 - Shipped"}})
    assert rows[0]["to_status"] is None
    print("  ✓ Rows without TO unaffected by enrichment")


if __name__ == "__main__":
    print("=== Phase B Logic Tests ===\n")
    test_column_map_completeness()
    test_parse_empty_xml()
    test_parse_single_row()
    test_parse_real_xml()
    print()
    test_enrichment()
    test_enrichment_missing_to()
    print("\n=== All tests passed ===")
