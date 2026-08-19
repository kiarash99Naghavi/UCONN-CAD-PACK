"""Per-run selection audit: did this run ship the best thing it built?

Every attempt that leaves geometry gets scored against ground truth after the fact
(`dashboard._attempt_gt_scores`), so for any finished run we can ask a question the
pipeline itself cannot: of everything this run built, was the thing it SHIPPED the
best one? Measured across 48 tasks the answer is no on 7 of them, worth 0.59 diff_f1.

The audit writes its verdict onto the run record so the dashboard can show it, and
classifies WHY, because the two causes need completely different fixes:

  degraded-by-later-subgoal
      An earlier sub-goal's ACCEPTED state scored higher than the final result. The
      selection inside each sub-goal was fine; a later sub-goal made the part worse
      and nothing checks for that. No within-sub-goal selector can recover this.

  rejected-was-better
      A candidate the pipeline REJECTED scored higher than what shipped. Recoverable
      only by a policy willing to ship a rejected candidate — which is a real risk,
      not free: rejections are usually right, and the same widening has been measured
      picking a 0.007 candidate over a 0.578 one elsewhere.

Ground truth is used only to grade, never to choose — this is an audit of decisions
already made, not a selector.

    python tools/selection_audit.py             # write onto run records
    python tools/selection_audit.py --dry-run   # report only
    python tools/selection_audit.py --strip     # remove the field again
"""

import argparse
import glob
import json
import os.path as osp
import sys

sys.path.insert(0, osp.dirname(osp.dirname(osp.abspath(__file__))))

FIELD = "selection_audit"
# Below this the two results are the same answer for scoring purposes.
EPS = 0.01


def audit(rec):
    """{shipped, best, gap, cause, ...} or None when the run built nothing scored."""
    shipped = (rec.get("scores") or {}).get("diff_f1")
    if shipped is None:
        return None
    best = None
    for s in (rec.get("steps") or []):
        g = (s.get("gt_scores") or {}).get("diff_f1")
        if g is None:
            continue
        if best is None or g > best[0]:
            best = (g, s)
    if best is None:
        return None

    g, s = best
    gap = g - shipped
    if gap <= EPS:
        return {"shipped": round(shipped, 6), "best": round(g, 6),
                "gap": round(gap, 6), "cause": "shipped-the-best",
                "best_sub": s.get("sub"), "best_attempt": s.get("attempt"),
                "best_verdict": s.get("verdict")}

    # An ACCEPTED candidate that beat the final result means the run was better
    # earlier than it ended — a later sub-goal undid work. A REJECTED one means the
    # judge threw away the best thing on the table.
    cause = ("degraded-by-later-subgoal"
             if s.get("verdict") in ("accepted", "partial")
             else "rejected-was-better")
    return {"shipped": round(shipped, 6), "best": round(g, 6),
            "gap": round(gap, 6), "cause": cause,
            "best_sub": s.get("sub"), "best_attempt": s.get("attempt"),
            "best_verdict": s.get("verdict")}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--strip", action="store_true")
    args = ap.parse_args()

    from src import config as c
    paths = sorted(glob.glob(osp.join(c.RESULTS, "dashboard_runs", "*.json")))

    if args.strip:
        n = 0
        for p in paths:
            rec = json.load(open(p))
            if rec.pop(FIELD, None) is not None:
                n += 1
                if not args.dry_run:
                    json.dump(rec, open(p, "w"), default=str)
        print(f"stripped {FIELD} from {n} run records")
        return 0

    rows, n = [], 0
    for p in paths:
        rec = json.load(open(p))
        a = audit(rec)
        if a is None:
            continue
        rec[FIELD] = a
        n += 1
        if not args.dry_run:
            json.dump(rec, open(p, "w"), default=str)
        rows.append((osp.basename(p)[:-5], a))

    by_cause = {}
    for _rid, a in rows:
        by_cause.setdefault(a["cause"], []).append(a["gap"])

    print(f"audited {n} runs{'  (dry run)' if args.dry_run else ''}\n")
    print("%-28s %6s %10s" % ("cause", "runs", "total gap"))
    for cause, gaps in sorted(by_cause.items(), key=lambda kv: -sum(kv[1])):
        print("%-28s %6d %10.4f" % (cause, len(gaps), sum(gaps)))
    print()
    losers = [(r, a) for r, a in rows if a["cause"] != "shipped-the-best"]
    print("%-36s %8s %8s %8s  %s" % ("task", "shipped", "best", "gap", "cause"))
    for rid, a in sorted(losers, key=lambda x: -x[1]["gap"]):
        print("%-36s %8.4f %8.4f %+8.4f  %s (sub %s try %s, %s)"
              % (rid[:36], a["shipped"], a["best"], a["gap"], a["cause"],
                 a["best_sub"], a["best_attempt"], a["best_verdict"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
