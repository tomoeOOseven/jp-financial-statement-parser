import copy, pathlib
import pytest
from fsparse import parse_statement, validate
from fsparse.core import parse_cell
from fsparse.mutation import detection_rates, mutate

TEXT = (pathlib.Path(__file__).parent / "page4.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def st():
    return parse_statement(TEXT)


def row(st, *path_tail):
    hits = [r for r in st.rows if r.path[-len(path_tail):] == path_tail]
    assert len(hits) == 1, hits
    return hits[0]


# ---- normalize
@pytest.mark.parametrize("tok,exp", [
    ("△1,234", (True, -1234)), ("▲1,234", (True, -1234)), ("△0", (True, 0)),
    ("－", (True, None)), ("-", (True, None)), ("0", (True, 0)),
    ("２，０２４", (True, 2024)), ("現金", (False, None)),
])
def test_parse_cell(tok, exp):
    assert parse_cell(tok) == exp


# ---- header
def test_header(st):
    assert st.periods == ["2025-03-31", "2026-03-31"]
    assert (st.unit, st.unit_multiplier) == ("千円", 1000)
    assert st.flags == ["corrected_version"]
    assert st.warnings == []


# ---- cells
def test_special_cells(st):
    assert row(st, "貸倒引当金").values == [-2979, 0]            # △0 is 0, not null
    assert row(st, "建設仮勘定").values == [None, 4553]          # － is null, not 0
    assert row(st, "長期貸付金").values == [None, 62000]
    assert row(st, "資産合計").values == [7534110, 5135249]


def test_paths_are_unique_and_disambiguate_repeated_labels(st):
    paths = [r.path for r in st.rows]
    assert len(paths) == len(set(paths))
    assert row(st, "有形固定資産", "リース資産(純額)").values == [63146, 41872]
    assert row(st, "無形固定資産", "リース資産").values == [34190, 21402]
    assert row(st, "投資その他の資産", "その他").values == [133361, 70967]
    assert row(st, "流動資産", "その他").values == [105260, 246895]


def test_detail_rows_excluded_from_subtotals(st):
    kinds = {r.label: r.kind for r in st.rows if "有形固定資産" in r.path[:-1]}
    assert kinds["建物及び構築物"] == "detail" and kinds["建物及び構築物(純額)"] == "item"
    assert kinds["土地"] == "item"


# ---- validators
def test_all_checks_pass_with_real_rounding_noise(st):
    checks = validate(st)
    assert all(c.ok for c in checks)
    d = {c.name: c.deltas for c in checks}
    # Real, legitimate rounding differences (千円) -- exact equality would wrongly reject this page.
    assert d["subtotal:流動資産合計"] == [-3, -2]
    assert d["subtotal:有形固定資産合計"] == [-1, -3]
    assert d["subtotal:資産合計"] == [0, -1]
    assert d["net:建物及び構築物(純額)"] == [0, 1]


def test_every_value_row_is_covered_by_a_check(st):
    validate(st)
    assert [r.label for r in st.rows if r.kind != "header" and r.confidence is None] == []


def test_exact_equality_would_reject_valid_page(st):
    assert not all(c.ok for c in validate(st, eps_per_addend=0))


def test_dropped_digit_is_caught(st):
    bad = copy.deepcopy(st)
    row(bad, "流動資産", "現金及び預金").values[0] = 200_429      # 2,002,429 with a digit dropped
    failed = [c.name for c in validate(bad) if not c.ok]
    assert "subtotal:流動資産合計" in failed


def test_sign_flip_is_caught(st):
    bad = copy.deepcopy(st)
    row(bad, "有形固定資産", "機械装置及び運搬具", "減価償却累計額").values[0] = 3_634_165
    assert any(not c.ok for c in validate(bad) if c.kind in ("sign", "net"))


def test_validator_detection_floor(st):
    """Regression guard on the validators: >=100% above the tolerance, and a recorded floor below it."""
    rates = detection_rates(st)
    assert all(h == t for k, (h, t) in rates.items() if k >= 2)
    assert rates[1][0] / rates[1][1] >= 0.70
