from copy import deepcopy
from pathlib import Path

import pytest

from fsparse import parse_statement, validate
from fsparse.extract import page_to_lines


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ("bs_before", "bs_after", "cf_before", "cf_after"))
def test_fixture_pdf_lines_match_gold_output(name: str):
    pdf_lines = page_to_lines(str(FIXTURES / f"{name}.pdf"), page=0)
    expected_lines = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8").splitlines()

    assert pdf_lines == expected_lines


def test_balance_sheet_before_and_after_have_same_rows_and_paths():
    before = parse_statement(
        (FIXTURES / "bs_before.txt").read_text(encoding="utf-8")
    )
    after = parse_statement(
        (FIXTURES / "bs_after.txt").read_text(encoding="utf-8")
    )

    assert before.statement == after.statement == "consolidated_balance_sheet"
    assert before.flags == ["pre_correction"]
    assert after.flags == ["corrected_version"]
    assert before.warnings == after.warnings == []
    assert [(row.label, row.path) for row in before.rows] == [
        (row.label, row.path) for row in after.rows
    ]
    differing_cells = [
        (before_row.label, period, before_value, after_value)
        for before_row, after_row in zip(before.rows, after.rows)
        for period, (before_value, after_value) in enumerate(
            zip(before_row.values, after_row.values)
        )
        if before_value != after_value
    ]
    assert differing_cells == [
        ("投資有価証券", 1, 53_925, 218_836),
        ("関係会社株式", 1, 166_033, 1_123),
    ]
    assert all(
        row.label not in {"<添付資料11ページ>", "(連結キャッシュ・フロー計算書)"}
        for row in before.rows
    )


@pytest.mark.parametrize(
    ("name", "expected_flag"),
    (("cf_before", "pre_correction"), ("cf_after", "corrected_version")),
)
def test_cash_flow_periods_and_reconciliations(name: str, expected_flag: str):
    statement = parse_statement(
        (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    )
    checks = validate(statement)

    assert statement.statement == "consolidated_cash_flow_statement"
    assert statement.periods == ["2025-03-31", "2026-03-31"]
    assert statement.flags == [expected_flag]
    assert statement.warnings == []
    assert len({row.path for row in statement.rows}) == len(statement.rows)
    assert all(check.ok for check in checks), [
        (check.name, check.deltas, check.eps) for check in checks if not check.ok
    ]
    cash_flow_checks = [check for check in checks if check.kind == "cash_flow"]
    assert len(cash_flow_checks) == 4


def test_cash_flow_net_change_corruption_fails_reconciliation():
    statement = parse_statement(
        (FIXTURES / "cf_after.txt").read_text(encoding="utf-8")
    )
    corrupted = deepcopy(statement)
    net_change = next(
        row for row in corrupted.rows if row.label == "現金及び現金同等物の増減額(△は減少)"
    )
    net_change.values[1] += 10_000

    failed = [check.name for check in validate(corrupted) if not check.ok]
    assert "cash_flow:net_change:2026-03-31" in failed
    assert "cash_flow:cash_balance:2026-03-31" in failed
