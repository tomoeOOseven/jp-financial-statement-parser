"""Measure the validators themselves: corrupt one digit at a time and see if any check fires."""
from __future__ import annotations
import copy
from .core import Statement, validate


def mutate(st: Statement, ridx: int, p: int, k: int) -> Statement:
    """Bump the 10^k digit of one cell (d -> d+1 mod 10)."""
    m = copy.deepcopy(st)
    v = m.rows[ridx].values[p]
    s = str(abs(v)); pos = len(s) - 1 - k
    new = int(s[:pos] + str((int(s[pos]) + 1) % 10) + s[pos + 1:])
    m.rows[ridx].values[p] = -new if v < 0 else new
    return m


def detection_rates(st: Statement, max_k: int = 7, **kw) -> dict:
    assert all(c.ok for c in validate(st, **kw)), "baseline page must pass"
    out = {}
    for k in range(max_k):
        tot = hit = 0
        for ri, r in enumerate(st.rows):
            for p, v in enumerate(r.values):
                if v is None or len(str(abs(v))) <= k:
                    continue
                tot += 1
                hit += any(not c.ok for c in validate(mutate(st, ri, p, k), **kw))
        out[k] = (hit, tot)
    return out
