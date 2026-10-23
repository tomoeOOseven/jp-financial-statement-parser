"""Regression tests for the four blind-eval root causes."""
import pathlib
import pytest
from fsparse import parse_statement, validate

FX = pathlib.Path(__file__).parent / "fixtures_blind"


def load(name):
    return parse_statement((FX / f"{name}.txt").read_text(encoding="utf-8"))


def by_name(checks):
    return {c.name: c for c in checks}


def test_repeated_header_does_not_add_periods():
    text = "(単位:千円)\n(2025年3月31日) (2026年3月31日)\nA 1 2\n(単位:千円)\n(2025年3月31日) (2026年3月31日)\nB 3 4"
    assert parse_statement(text).periods == ["2025-03-31", "2026-03-31"]


def test_bare_page_number_is_not_a_row():
    st = parse_statement("(単位:千円)\n(2025年3月31日) (2026年3月31日)\nA 1 2\n7")
    assert [r.label for r in st.rows] == ["A"]


@pytest.mark.parametrize("name", ["afc_bs_before", "afc_bs_after"])
def test_total_without_header_closes_preceding_siblings(name):
    st = load(name)
    assert st.warnings == []
    checks = by_name(validate(st))
    parent = checks["subtotal:親会社の所有者に帰属する持分合計"]
    assert len(parent.rows) - 1 == 5                       # 資本金 .. その他の資本の構成要素
    assert len(checks["subtotal:資本合計"].rows) - 1 == 2   # parent subtotal + 非支配持分
    assert checks["subtotal:負債及び資本合計"].deltas == [0, 0]


@pytest.mark.parametrize("name, operating", [
    ("sanwa_cf_before", [-1, 0]),
    ("sanwa_cf_after", [-1, -1]),
])
def test_cash_flow_deltas(name, operating):
    st = load(name)
    assert st.statement == "consolidated_cash_flow_statement"   # after-slice has no title line
    c = by_name(validate(st))
    op = "subtotal:営業活動によるキャッシュ・フロー"
    assert c[op + ":小計"].deltas == [0, 2]
    assert c[op].deltas == operating
    assert c["subtotal:投資活動によるキャッシュ・フロー"].deltas == [1, 0]
    assert c["subtotal:財務活動によるキャッシュ・フロー"].deltas == [1, 2]
    assert [c[f"cash_flow:net_change:{p}"].deltas[0] for p in st.periods] == [0, -1]
    assert [c[f"cash_flow:cash_balance:{p}"].deltas[0] for p in st.periods] == [0, 0]


def test_cash_flow_catches_dropped_digit():
    text = (FX / "sanwa_cf_after.txt").read_text(encoding="utf-8")
    text = text.replace("885,445", "88,545")                # corrupt the consolidation adjustment
    bad = [c.name for c in validate(parse_statement(text)) if not c.ok]
    assert "cash_flow:cash_balance:2026-03-31" in bad
