# Japanese financial-statement page → validated table

Take-home for the Ur AI Document Intelligence & VLM R&D internship.

Given a page from a Japanese filing (決算短信 etc.), produce a table where every row has a
hierarchical path, one value per period, the raw string it came from, and a confidence
score derived from accounting checks the page itself implies.

```
PDF page ─► extract (pdfplumber words → lines) ─► normalize (NFKC, △→−, －→null)
        ─► header (unit, periods, statement type, 訂正前/後 flags)
        ─► row parse + hierarchy (section stack, 合計 closes sections, 純額 groups)
        ─► validate (subtotals, net, identities, cash flow, sign, shape, cross-period)
        ─► per-row confidence  →  accept / send to review
```

## Running it

```bash
pip install -r requirements-dev.txt
python -m pytest -q                                      # 91 tests
python run_demo.py tests/page4.txt                    # the page from the brief
python run_demo.py tests/fixtures_blind/sanwa_cf_after.txt --json
```

From a PDF: `fsparse.extract.page_to_lines(pdf_path, page_index)` gives the lines that
`fsparse.parse_statement` takes.

## What "correct" means

A table can look right and still be wrong, so correctness is checked in three layers:

1. **Structure.** Each row has the right label, the right parent (row identity is the *path*,
   e.g. 固定資産 > 有形固定資産 > リース資産, because labels like その他 and 減価償却累計額 repeat),
   and exactly one value per period.
2. **Values.** Each cell equals what the page prints: `△1,234` → −1234, `－` → null (never 0),
   unit kept explicitly (千円 / 百万円), raw string stored next to the parsed value.
3. **Internal consistency.** The table obeys the arithmetic the page implies. This needs no
   ground truth, so it runs on every page in production:
   - subtotal: Σ children ≈ 合計 (including 小計 splits in cash-flow statements)
   - net: gross + 減価償却累計額 ≈ （純額）
   - identities: 負債合計 + 純資産合計 = 負債純資産合計 = 資産合計; P/L tax and attribution chains
   - cash flow: 営業 + 投資 + 財務 (+ FX) = 増減額; opening + 増減額 + consolidation adjustments = closing
   - shape, sign (contra-asset rows ≤ 0), cross-period jumps ≥ 1000×

**Tolerance.** Filings round each line independently, so sums are legitimately off by a few
units (on the brief's page, current assets sum to 5,863,681 vs a printed 5,863,684). A check
passes when |Δ| ≤ 1 × (number of addends). Exact equality would reject valid pages; a dropped
or swapped digit produces an error far above this.

## How it is measured

| Metric | Needs gold? | Use |
|---|---|---|
| Cell accuracy: exact match on (label, values) per row | yes | headline accuracy |
| Page-exact rate: pages with zero cell errors | yes | what a downstream user cares about |
| Validator pass rate | no | production monitoring |
| Error-detection rate: corrupt one digit, does any check fire? (`mutation.py`) | no | measures the validators themselves |

Policy: a wrong number entering a database silently is worse than a page sent to review, so a
page is auto-accepted only when every check passes.

## Results

Fixtures are real 訂正 filings from 5 companies, before and after correction: J-GAAP and IFRS,
千円 and 百万円, balance sheet, income statement, comprehensive income, cash flow, multi-page
statements and repeated mid-page headers.

| Set | Tests | Result |
|---|---|---|
| Brief's page + design fixtures (Yamato, Enjin) | 48 | pass |
| Blind set (AI Fusion IFRS, 三和油化, ビースタイル ×2; 12 statements, 437 gold rows) | 36 | pass |
| Regression tests for the blind-set fixes | 7 | pass |

**The blind set is no longer blind.** It was written against an independent gold reader and
first run after the parser was designed: extraction passed 12/12, but parsing and validation
failed 14 tests. Those traced to 4 root causes, each fixed generally, not per company:

1. A header repeated mid-page added duplicate periods (4 instead of 2), which made every row
   fail the shape check.
2. A bare page number on its own line was parsed as a row.
3. An IFRS total with no matching section header (親会社の所有者に帰属する持分合計) did not
   close the rows above it.
4. Cash-flow rules were skipped when a slice lacked the statement title; the closing-cash rule
   only knew 連結除外 (decrease), not 新規連結 (increase).

**Error detection on the brief's page** (one digit corrupted at a time):

| Digit position | 1s | 10s | 100s and up |
|---|---|---|---|
| Caught | 15% | 76% | 100% |

Errors in the last one or two digits mostly hide inside the rounding tolerance. That is the
main known gap: those pages would need a second extractor or a VLM reading to cross-check.

## Limits / not built yet

- Scanned pages (OCR fallback), side-by-side asset/liability layouts, labels wrapped over two lines.
- Some rules name specific accounts (負債純資産合計, 法人税等合計, the three CF sections); a new
  filer's wording may need additions.
- Repair step (try single-cell alternatives, accept only if all checks pass) is designed, not implemented.

## Layout

```
fsparse/
  core.py        normalize, header, row parse, hierarchy, validators
  extract.py     PDF words → text lines (pdfplumber)
  mutation.py    error-detection measurement
tests/           fixtures (real filing PDFs + text), gold rows, tests
run_demo.py      parse one page, print checks and error-detection rates
docs/copilot-instructions.md   instructions given to the coding assistant
ai_logs/         AI session log (readable .md + unedited .raw.jsonl)
```
