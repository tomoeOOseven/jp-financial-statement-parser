import json, sys, pathlib
from fsparse import parse_statement, validate, to_dict
from fsparse.mutation import detection_rates

st = parse_statement(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
checks = validate(st)
print("unit", st.unit, "| periods", st.periods, "| flags", st.flags, "| warnings", st.warnings)
print(f"{len(st.rows)} rows\n")
for c in checks:
    if c.kind in ("subtotal", "net"):
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name:<28} deltas={c.deltas}  eps={c.eps}")
print("\nrows covered by no check:", [r.label for r in st.rows if r.kind != "header" and r.confidence is None])
print("\nsingle-digit corruption detection rate by digit position (10^k):")
for k, (h, t) in detection_rates(st).items():
    if t:                                   # small-unit pages (百万円) have no cells at high k
        print(f"  k={k}: {h}/{t} = {h/t:.0%}")
if "--json" in sys.argv:
    print(json.dumps(to_dict(st), ensure_ascii=False, indent=1))
