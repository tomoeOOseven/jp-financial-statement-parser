"""Blind evaluation on 4 companies (AI Fusion IFRS/百万円, 三和油化, ビースタイル x2).
Fixtures were NOT used to design the parser. Failures here are findings, not test bugs.
Gold rows come from an independent mini-reader (gold_rows.json), not from fsparse.core."""
import difflib, json, pathlib
import pdfplumber, pytest
from fsparse import parse_statement, validate
from fsparse.extract import page_to_lines          # zero-based page index

FX = pathlib.Path(__file__).parent / "fixtures_blind"
GOLD = json.loads((FX / "gold_rows.json").read_text(encoding="utf-8"))
NAMES = sorted(GOLD)


def pdf_for(n): return FX / ("sanwa_ci_pages.pdf" if n.startswith("sanwa_ci") else f"{n}.pdf")
def gold_lines(n): return (FX / f"{n}.txt").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("n", NAMES)
def test_extraction_contains_gold_lines(n):
    """Gold lines must appear as one contiguous run in the extractor output (some PDFs hold both
    versions or narrative text, so the PDF yields more lines than the fixture)."""
    with pdfplumber.open(pdf_for(n)) as pdf:
        got = [l for i in range(len(pdf.pages)) for l in page_to_lines(str(pdf_for(n)), i)]
    gold = gold_lines(n)
    assert gold[0] in got, f"first gold line not found: {gold[0]!r}"
    s = got.index(gold[0])
    diff = [d for d in difflib.unified_diff(gold, got[s:s + len(gold)], "gold", "extracted", lineterm="", n=0)]
    assert got[s:s + len(gold)] == gold, "\n".join(diff[:30])


@pytest.mark.parametrize("n", NAMES)
def test_parsed_cells_match_independent_gold(n):
    st = parse_statement("\n".join(gold_lines(n)))
    assert len(st.periods) == 2, st.periods
    got = [[r.label, *r.values] for r in st.rows if r.values]
    want = GOLD[n]["rows"]
    ok = sum(1 for g, w in zip(got, want) if g == w)
    print(f"{n}: cell-row accuracy {ok}/{len(want)}")
    assert got == want, "\n".join(list(difflib.unified_diff(
        [str(w) for w in want], [str(g) for g in got], "gold", "parsed", lineterm="", n=0))[:30])


@pytest.mark.parametrize("n", NAMES)
def test_validators_pass(n):
    st = parse_statement("\n".join(gold_lines(n)))
    bad = [(c.name, c.deltas) for c in validate(st) if not c.ok]
    assert not bad, bad
