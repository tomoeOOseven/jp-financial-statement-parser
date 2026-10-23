"""Parser for text-layer Japanese financial statement lines.

Stages implemented: normalize, header parse, row parse, hierarchy, validate.
(Extraction/layout from PDF is upstream: feed this the page's text lines.)
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

NULL_TOKENS = {"-", "―", "—"}          # NFKC turns full-width "－" into "-"
NUM_RE = re.compile(r"^[△▲-]?\d[\d,]*$")
DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
UNIT_RE = re.compile(r"単位\s*:\s*(円|千円|百万円|億円)")
UNIT_MULT = {"円": 1, "千円": 1_000, "百万円": 1_000_000, "億円": 100_000_000}
HEADER_HINT = re.compile(
    r"単位|\d{4}\s*年|連結会計年度|訂正(?:前|後)|財務諸表|"
    r"訂正箇所|貸借対照表|損益計算書|包括利益計算書|キャッシュ・フロー計算書|"
    r"^<添付資料.*ページ>$|^\(?\d+[.)]|^\([^)]*\)$|^-\s*\d+\s*-$|^以上$"
)
PAGE_NO_RE = re.compile(r"^\d{1,3}$")
DATE_RANGE_END_RE = re.compile(r"至\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# e.g. 新規連結に伴う…の増加額 / 連結除外に伴う…の減少額 (values carry their own sign)
CASH_ADJ_RE = re.compile(r"に伴う現金及び現金同等物の(?:増加|減少)額")
CF_SECTION_LABELS = {
    "営業活動によるキャッシュ・フロー",
    "投資活動によるキャッシュ・フロー",
    "財務活動によるキャッシュ・フロー",
}


# ---------------------------------------------------------------- normalize
def nfkc(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip()


def parse_cell(tok: str):
    """-> (is_numeric_token, value). '△1,234' -> -1234; '－' -> None (NOT 0)."""
    t = nfkc(tok)
    if t in NULL_TOKENS:
        return True, None
    if NUM_RE.match(t):
        n = int(t.lstrip("△▲-").replace(",", ""))
        return True, -n if t[0] in "△▲-" else n
    return False, None


# ---------------------------------------------------------------- data model
@dataclass
class Row:
    path: tuple
    label: str
    kind: str                      # header | item | detail | subtotal | total
    raw: list
    values: list
    line_no: int
    net_of: int | None = None      # detail rows point at their （純額） row
    confidence: float | None = None


@dataclass
class Check:
    name: str
    kind: str                      # shape | subtotal | net | sign | cross_period | identity | income | cash_flow
    ok: bool
    deltas: list
    eps: int
    rows: list                     # line_nos involved


@dataclass
class Statement:
    periods: list
    unit: str | None
    unit_multiplier: int
    flags: list
    rows: list
    warnings: list = field(default_factory=list)
    statement: str | None = None


# ---------------------------------------------------------------- row parse
def split_row(line: str, n: int):
    toks = line.split(" ")
    raw = []
    while toks and len(raw) < n:
        if not parse_cell(toks[-1])[0]:
            break
        raw.insert(0, toks.pop())
    return "".join(toks), raw


def _base(s: str) -> str:
    return s[:-2] if s.endswith("の部") else s


def _group_preceding_siblings(rows: list, section: tuple, name: str) -> int:
    """Move the rows directly above (back to the last header or subtotal in `section`)
    under section + (name,). Returns how many rows were moved."""
    k = len(section)
    j = len(rows)
    while j > 0:
        r = rows[j - 1]
        if r.kind in ("header", "subtotal", "total") or r.path[:k] != section or len(r.path) <= k:
            break
        j -= 1
    for r in rows[j:]:
        r.path = section + (name,) + r.path[k:]
    return len(rows) - j


def _statement_type(lines: list[str]) -> str | None:
    titles = (
        ("キャッシュ・フロー計算書", "consolidated_cash_flow_statement"),
        ("貸借対照表", "consolidated_balance_sheet"),
        ("損益計算書", "consolidated_income_statement"),
        ("包括利益計算書", "consolidated_comprehensive_income_statement"),
    )
    candidates = [line for line in lines if any(title in line for title, _ in titles)]
    numbered = [line for line in candidates if re.match(r"^\(\d+\)", line)]
    for line in numbered or candidates:
        for title, statement in titles:
            if title in line:
                return statement
    return None


def parse_statement(text: str) -> Statement:
    lines = [l for l in (nfkc(x) for x in text.splitlines()) if l]
    head = [l for l in lines if HEADER_HINT.search(l)]
    # a bare number on its own line is a page footer, not a row
    body = [l for l in lines if not HEADER_HINT.search(l) and not PAGE_NO_RE.match(l)]
    warnings: list = []

    range_dates = [date for line in head for date in DATE_RANGE_END_RE.findall(line)]
    date_parts = range_dates or [date for line in head for date in DATE_RE.findall(line)]
    # a header repeated mid-page or on a continuation page must not add periods
    periods = list(dict.fromkeys(f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in date_parts))
    unit = next((m.group(1) for l in head if (m := UNIT_RE.search(l))), None)
    flags = []
    if any("訂正後" in line for line in head):
        flags.append("corrected_version")
    if any("訂正前" in line for line in head):
        flags.append("pre_correction")
    statement = _statement_type(head)
    if statement is None and sum(l.split(" ")[0] in CF_SECTION_LABELS for l in body) >= 2:
        # continuation/corrected slices may lack the title line; the section labels identify a CF
        statement = "consolidated_cash_flow_statement"

    counts = Counter(len(split_row(l, 9)[1]) for l in body)
    counts.pop(0, None)
    n = len(periods) or (counts.most_common(1)[0][0] if counts else 0)
    if len(periods) != n:
        warnings.append(f"period count {len(periods)} != value columns {n}")
    if unit is None:
        warnings.append("unit not found")

    rows: list[Row] = []
    stack: list[str] = []
    for i, line in enumerate(body):
        label, raw = split_row(line, n)
        if re.search(r"\d", label) and not re.match(r"^\d+年(?:内|以内)", label):
            warnings.append(f"line {i}: digits left in label {label!r}")
        if not raw:                                   # section header
            if label == "以上":
                continue
            if label.startswith("※訂正箇所"):
                continue
            if label in {"資産の部", "負債の部", "純資産の部"}:
                stack.clear()
            stack.append(label)
            rows.append(Row(tuple(stack), label, "header", [], [], i))
            continue
        values = [parse_cell(t)[1] for t in raw]
        if label in CF_SECTION_LABELS and stack and stack[-1] == label:
            rows.append(Row(tuple(stack) + (label,), label, "subtotal", raw, values, i))
            stack.pop()
            continue
        if label.endswith("合計"):
            if label == "負債純資産合計":
                rows.append(Row(tuple(stack) + (label,), label, "total", raw, values, i))
                continue
            if label == "法人税等合計":
                rows.append(Row((label,), label, "subtotal", raw, values, i))
                continue
            name = label[:-2]
            idx = next((j for j in range(len(stack) - 1, -1, -1) if _base(stack[j]) == name), None)
            if idx is None:
                # No matching header (e.g. IFRS 親会社の所有者に帰属する持分合計): the total closes
                # the run of sibling rows just above it, so group them under an implicit section.
                path = tuple(stack) + (name,)
                if not _group_preceding_siblings(rows, tuple(stack), name):
                    warnings.append(f"line {i}: no open section for {label!r}")
            else:
                path = tuple(stack[: idx + 1])
                del stack[idx:]                       # close the section
            # subtotal path = its section path + own label, so it never collides with the header row
            rows.append(Row(path + (label,), label, "subtotal", raw, values, i))
            continue
        row = Row(tuple(stack) + (label,), label, "item", raw, values, i)
        if label.endswith("(純額)") and len(rows) >= 2:
            base = label[:-4]
            acc, gross = rows[-1], rows[-2]
            if acc.label == "減価償却累計額" and gross.label == base:
                gross.kind = acc.kind = "detail"
                gross.net_of = acc.net_of = i
                gross.path = tuple(stack) + (base, "取得価額")      # keep paths unique
                acc.path = tuple(stack) + (base, "減価償却累計額")
            else:
                warnings.append(f"line {i}: {label!r} without gross/accumulated rows")
        rows.append(row)
    if stack:
        warnings.append(f"unclosed sections: {stack}")
    return Statement(
        periods, unit, UNIT_MULT.get(unit or "", 1), flags, rows, warnings, statement
    )


# ---------------------------------------------------------------- validate
def _v(r: Row, p: int):
    return r.values[p] if p < len(r.values) else None


def _rows_named(st: Statement, label: str) -> list[Row]:
    return [row for row in st.rows if row.label == label and row.kind != "header"]


def _identity_check(
    st: Statement,
    name: str,
    rows: list[Row],
    delta: Callable[[list[int | None]], int],
    eps: int,
    kind: str = "identity",
) -> Check | None:
    if not rows or any(len(row.values) != len(st.periods) for row in rows):
        return None
    deltas = [
        delta([_v(row, period) for row in rows])
        for period in range(len(st.periods))
    ]
    return Check(
        name,
        kind,
        all(abs(value) <= eps for value in deltas),
        deltas,
        eps,
        [row.line_no for row in rows],
    )


def _add_statement_identity_checks(st: Statement, checks: list[Check]) -> None:
    if st.statement == "consolidated_balance_sheet":
        labels = ("負債合計", "純資産合計", "負債純資産合計", "資産合計")
        rows = [_rows_named(st, label) for label in labels]
        if all(len(matches) == 1 for matches in rows):
            liabilities, equity, combined, assets = [matches[0] for matches in rows]
            check = _identity_check(
                st,
                "identity:liabilities_equity",
                [liabilities, equity, combined],
                lambda values: (values[0] or 0) + (values[1] or 0) - (values[2] or 0),
                2,
            )
            if check is not None:
                checks.append(check)
            check = _identity_check(
                st,
                "identity:assets",
                [combined, assets],
                lambda values: (values[0] or 0) - (values[1] or 0),
                2,
            )
            if check is not None:
                checks.append(check)
        return

    if st.statement not in (
        "consolidated_income_statement",
        "consolidated_comprehensive_income_statement",
    ):
        return

    for label in ("営業外収益合計", "営業外費用合計"):
        subtotal = _rows_named(st, label)
        if len(subtotal) != 1:
            continue
        children = [
            row for row in st.rows
            if row.kind == "item" and row.path[:-1] == subtotal[0].path[:-1]
        ]
        if children:
            check = _identity_check(
                st,
                f"income:{label}",
                children + subtotal,
                lambda values: sum(value or 0 for value in values[:-1]) - (values[-1] or 0),
                len(children),
                "income",
            )
            if check is not None:
                checks.append(check)

    tax_lines = [
        _rows_named(st, "法人税、住民税及び事業税"),
        _rows_named(st, "法人税等調整額"),
        _rows_named(st, "法人税等合計"),
    ]
    if all(len(matches) == 1 for matches in tax_lines):
        tax_rows = [matches[0] for matches in tax_lines]
        check = _identity_check(
            st,
            "income:tax_total",
            tax_rows,
            lambda values: (values[0] or 0) + (values[1] or 0) - (values[2] or 0),
            2,
            "income",
        )
        if check is not None:
            checks.append(check)

    pretax = _rows_named(st, "税金等調整前当期純利益")
    taxes = _rows_named(st, "法人税等合計")
    net_income = _rows_named(st, "当期純利益")
    parent_income = _rows_named(st, "親会社株主に帰属する当期純利益")
    minority_income = [
        row for row in st.rows
        if row.kind != "header" and row.label.startswith("非支配株主に帰属する当期純")
    ]
    if len(pretax) == len(taxes) == 1 and net_income:
        check = _identity_check(
            st,
            "income:net_income",
            [pretax[0], taxes[0], net_income[0]],
            lambda values: (values[0] or 0) - (values[1] or 0) - (values[2] or 0),
            2,
            "income",
        )
        if check is not None:
            checks.append(check)
    if (
        len(net_income) == 1
        and len(parent_income) == 1
        and len(minority_income) == 1
    ):
        check = _identity_check(
            st,
            "income:parent_attribution",
            [net_income[0], minority_income[0], parent_income[0]],
            lambda values: (values[0] or 0) - (values[1] or 0) - (values[2] or 0),
            2,
            "income",
        )
        if check is not None:
            checks.append(check)

    comprehensive = _rows_named(st, "包括利益")
    parent_comprehensive = _rows_named(st, "親会社株主に係る包括利益")
    minority_comprehensive = _rows_named(st, "非支配株主に係る包括利益")
    if (
        len(comprehensive) == 1
        and len(parent_comprehensive) == 1
        and len(minority_comprehensive) == 1
    ):
        check = _identity_check(
            st,
            "income:comprehensive_attribution",
            [parent_comprehensive[0], minority_comprehensive[0], comprehensive[0]],
            lambda values: (values[0] or 0) + (values[1] or 0) - (values[2] or 0),
            2,
            "income",
        )
        if check is not None:
            checks.append(check)

    if len(net_income) >= 2:
        check = _identity_check(
            st,
            "income:profit_to_comprehensive",
            [net_income[0], net_income[1]],
            lambda values: (values[0] or 0) - (values[1] or 0),
            2,
            "income",
        )
        if check is not None:
            checks.append(check)


def validate(st: Statement, eps_per_addend: int = 1):
    """Tolerance: |delta| <= eps_per_addend * (#addends). Filings round each line
    independently, so exact equality would reject valid pages."""
    n = len(st.periods)
    checks: list[Check] = []
    by_line = {r.line_no: r for r in st.rows}

    for r in st.rows:                                                   # shape
        if r.kind != "header" and len(r.values) != n:
            checks.append(Check(f"shape:{r.label}", "shape", False, [], 0, [r.line_no]))

    for s in st.rows:                                                   # subtotal rule
        if s.kind != "subtotal":
            continue
        if s.label == "法人税等合計":
            continue
        sec = s.path[:-1]                                  # the section this subtotal closes
        kids = [r for r in st.rows if r is not s and (
                    (r.kind == "item" and r.path[:-1] == sec) or
                    (r.kind == "subtotal" and r.path[:-2] == sec))]
        small = next((r for r in kids if r.label == "小計"), None)
        if small is not None:
            # 小計 summarizes the rows above it; the section total is 小計 + the rows below it.
            # Never count 小計 together with the items it summarizes.
            above = [r for r in kids if r.line_no < small.line_no]
            if above:
                e = eps_per_addend * len(above)
                d = [sum(_v(k, p) or 0 for k in above) - (_v(small, p) or 0) for p in range(n)]
                checks.append(Check(f"subtotal:{s.label}:小計", "subtotal", all(abs(x) <= e for x in d),
                                    d, e, [small.line_no] + [k.line_no for k in above]))
            kids = [r for r in kids if r.line_no >= small.line_no]
        if not kids:
            continue
        eps = eps_per_addend * len(kids)
        deltas = [sum(_v(k, p) or 0 for k in kids) - (_v(s, p) or 0) for p in range(n)]
        checks.append(Check(f"subtotal:{s.label}", "subtotal", all(abs(d) <= eps for d in deltas),
                            deltas, eps, [s.line_no] + [k.line_no for k in kids]))

    for net in (r for r in st.rows if r.kind == "item" and r.label.endswith("(純額)")):   # net rule
        det = [r for r in st.rows if r.net_of == net.line_no]
        if not det:
            continue
        eps = eps_per_addend * 2
        deltas = [sum(_v(d, p) or 0 for d in det) - (_v(net, p) or 0) for p in range(n)]
        checks.append(Check(f"net:{net.label}", "net", all(abs(d) <= eps for d in deltas),
                            deltas, eps, [net.line_no] + [d.line_no for d in det]))

    for r in st.rows:                                                   # sign sanity
        if (
            st.statement == "consolidated_balance_sheet"
            and r.kind != "subtotal"
            and "資産の部" in r.path
            and r.label in {"減価償却累計額", "貸倒引当金"}
        ):
            ok = all((v is None or v <= 0) for v in r.values)
            checks.append(Check(f"sign:{r.label}", "sign", ok, [], 0, [r.line_no]))

    for r in st.rows:                                                   # cross-period 1000x
        for p in range(1, len(r.values)):
            a, b = r.values[p - 1], r.values[p]
            if a and b:
                ratio = max(abs(a), abs(b)) / min(abs(a), abs(b))
                checks.append(Check(f"xperiod:{r.label}", "cross_period", ratio < 1000, [], 0, [r.line_no]))

    _add_statement_identity_checks(st, checks)

    if st.statement == "consolidated_cash_flow_statement":
        by_label: dict[str, list[Row]] = {}
        for row in st.rows:
            by_label.setdefault(row.label, []).append(row)

        def one_value(label: str, period: int, kind: str | None = None):
            matches = by_label.get(label, [])
            if kind is not None:
                matches = [row for row in matches if row.kind == kind]
            return _v(matches[0], period) if len(matches) == 1 else None

        operating = "営業活動によるキャッシュ・フロー"
        investing = "投資活動によるキャッシュ・フロー"
        financing = "財務活動によるキャッシュ・フロー"
        exchange = "現金及び現金同等物に係る換算差額"
        change = "現金及び現金同等物の増減額(△は減少)"
        beginning = "現金及び現金同等物の期首残高"
        ending = "現金及び現金同等物の期末残高"
        for period in range(n):
            components = [
                one_value(label, period, "subtotal")
                for label in (operating, investing, financing)
            ]
            fx = one_value(exchange, period) if exchange in by_label else 0
            fx = fx or 0
            printed_change = one_value(change, period)
            if all(value is not None for value in components + [fx, printed_change]):
                rows_involved = [
                    row.line_no
                    for label in (operating, investing, financing, exchange, change)
                    for row in by_label.get(label, [])
                    if row.kind != "header"
                ]
                delta = sum(components) + fx - printed_change
                eps = len(components) + int(exchange in by_label)
                checks.append(
                    Check(
                        f"cash_flow:net_change:{st.periods[period]}",
                        "cash_flow",
                        abs(delta) <= eps,
                        [delta],
                        eps,
                        rows_involved,
                    )
                )

            opening_value = one_value(beginning, period)
            ending_value = one_value(ending, period)
            consolidation_adjustment = sum(
                value
                for label, rows in by_label.items()
                if CASH_ADJ_RE.search(label)
                for row in rows
                if (value := _v(row, period)) is not None
            )
            if (
                opening_value is not None
                and printed_change is not None
                and ending_value is not None
            ):
                delta = (
                    opening_value + printed_change + consolidation_adjustment - ending_value
                )
                eps = 2 + int(consolidation_adjustment != 0)
                checks.append(
                    Check(
                        f"cash_flow:cash_balance:{st.periods[period]}",
                        "cash_flow",
                        abs(delta) <= eps,
                        [delta],
                        eps,
                        [
                            row.line_no
                            for label in (beginning, change, ending)
                            for row in by_label.get(label, [])
                            if row.kind != "header"
                        ]
                        + [
                            row.line_no
                            for label, rows in by_label.items()
                            if CASH_ADJ_RE.search(label)
                            for row in rows
                            if _v(row, period) is not None
                        ],
                    )
                )

    hits: dict[int, list[bool]] = {}
    for c in checks:
        for ln in c.rows:
            hits.setdefault(ln, []).append(c.ok)
    for r in st.rows:
        h = hits.get(r.line_no)
        r.confidence = None if not h else sum(h) / len(h)
    return checks


def to_dict(st: Statement) -> dict:
    return {
        "statement": st.statement, "unit": st.unit, "unit_multiplier": st.unit_multiplier,
        "periods": st.periods, "flags": st.flags, "warnings": st.warnings,
        "rows": [{"path": list(r.path), "label": r.label, "kind": r.kind, "raw": r.raw,
                  "values": r.values, "confidence": r.confidence} for r in st.rows],
    }
