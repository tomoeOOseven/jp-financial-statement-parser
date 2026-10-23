from pathlib import Path

import pytest

from fsparse import parse_statement, validate
from fsparse.core import parse_cell
from fsparse.extract import join_pages, page_to_lines


FIXTURES = Path(__file__).parent / "fixtures_enjin"


@pytest.mark.parametrize(
    "name",
    (
        "bs_before_1",
        "bs_before_2",
        "bs_after_1",
        "bs_after_2",
        "pl_before",
        "pl_after",
        "ci_before",
        "ci_after",
    ),
)
def test_enjin_fixture_pdf_lines_match_text(name: str):
    actual = page_to_lines(str(FIXTURES / f"{name}.pdf"), page=0)
    expected = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8").splitlines()

    assert actual == expected


@pytest.mark.parametrize(
    ("version", "flag"),
    (("before", "pre_correction"), ("after", "corrected_version")),
)
def test_joined_balance_sheet_pages_keep_first_page_metadata(version: str, flag: str):
    pages = [
        page_to_lines(str(FIXTURES / f"bs_{version}_{page}.pdf"), page=0)
        for page in (1, 2)
    ]
    statement = parse_statement("\n".join(join_pages(pages)))
    checks = validate(statement)

    assert statement.statement == "consolidated_balance_sheet"
    assert statement.periods == ["2025-05-31", "2026-05-31"]
    assert statement.flags == [flag]
    assert statement.warnings == []
    assert not [check for check in checks if check.kind == "shape" and not check.ok]
    assert len({row.path for row in statement.rows}) == len(statement.rows)
    identities = {check.name: check for check in checks if check.kind == "identity"}
    assert identities["identity:liabilities_equity"].deltas == [-1, 0]
    assert identities["identity:liabilities_equity"].eps == 2
    assert identities["identity:assets"].deltas == [0, 0]
    assert identities["identity:assets"].eps == 2


def test_balance_sheet_sign_checks_only_asset_contra_items():
    lines = join_pages([
        (FIXTURES / "bs_after_1.txt").read_text(encoding="utf-8").splitlines(),
        (FIXTURES / "bs_after_2.txt").read_text(encoding="utf-8").splitlines(),
    ])
    statement = parse_statement("\n".join(lines))
    sign_checks = [check for check in validate(statement) if check.kind == "sign"]
    checked_rows = [
        row for row in statement.rows
        if any(row.line_no in check.rows for check in sign_checks)
    ]

    assert sign_checks
    assert all(check.ok for check in sign_checks)
    assert {row.label for row in checked_rows} == {"減価償却累計額", "貸倒引当金"}
    assert all("資産の部" in row.path for row in checked_rows)
    assert all(row.kind != "subtotal" for row in checked_rows)
    assert not any(
        "賞与引当金" in row.label
        or "ポイント引当金" in row.label
        or "その他の包括利益累計額合計" in row.label
        for row in checked_rows
    )


def test_p_and_l_identities_before_and_after():
    expected_parent_deltas = {
        "pl_before": [0, 1],
        "pl_after": [0, 0],
    }
    expected_flags = {
        "pl_before": "pre_correction",
        "pl_after": "corrected_version",
    }
    for name, parent_deltas in expected_parent_deltas.items():
        statement = parse_statement(
            (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
        )
        checks = validate(statement)
        by_name = {check.name: check for check in checks}

        assert statement.statement == "consolidated_income_statement"
        assert statement.flags == [expected_flags[name]]
        assert by_name["subtotal:営業外収益合計"].deltas == [-2, -2]
        assert by_name["subtotal:営業外費用合計"].deltas == [-2, -3]
        assert by_name["income:net_income"].deltas == [1, 1]
        assert by_name["income:parent_attribution"].deltas == parent_deltas
        assert all(check.ok for check in checks)
        assert not any("法人税等合計" in warning for warning in statement.warnings)


def test_comprehensive_income_identities_and_cross_statement_profit():
    for version, expected_deltas in (
        ("before", [0, -1]),
        ("after", [0, 0]),
    ):
        ci = parse_statement(
            (FIXTURES / f"ci_{version}.txt").read_text(encoding="utf-8")
        )
        ci_checks = {check.name: check for check in validate(ci)}
        assert ci.statement == "consolidated_comprehensive_income_statement"
        assert ci_checks["income:comprehensive_attribution"].deltas == expected_deltas
        assert all(check.ok for check in ci_checks.values())

        pages = [
            (FIXTURES / f"pl_{version}.txt").read_text(encoding="utf-8").splitlines(),
            (FIXTURES / f"ci_{version}.txt").read_text(encoding="utf-8").splitlines(),
        ]
        combined = parse_statement("\n".join(join_pages(pages)))
        combined_checks = {check.name: check for check in validate(combined)}
        assert combined_checks["income:profit_to_comprehensive"].deltas == [0, 0]
        assert all(check.ok for check in combined_checks.values())


def test_null_tokens_all_parse_as_none():
    assert [parse_cell(token) for token in ("-", "－", "―")] == [
        (True, None),
        (True, None),
        (True, None),
    ]


def test_year_within_label_does_not_create_digit_warning():
    page_two = (FIXTURES / "bs_before_2.txt").read_text(encoding="utf-8")
    statement = parse_statement(
        "\n".join(join_pages([
            (FIXTURES / "bs_before_1.txt").read_text(encoding="utf-8").splitlines(),
            page_two.splitlines(),
        ]))
    )

    assert not any("1年内返済予定の長期借入金" in warning for warning in statement.warnings)
