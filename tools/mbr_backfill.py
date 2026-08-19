"""Backfill MBR consensus onto runs that finished BEFORE the selector existed.

The 48 archived runs predate `SELECTION_POLICY=mbr`, so none of their step records
carry a consensus score and the dashboard has nothing to draw. Their candidate
geometry is still on disk, though, so the consensus can be computed after the fact —
no model calls, no re-running the pipeline.

What this does NOT do is change any result. A backfilled pick is what MBR *would*
have chosen had it been running; the geometry that shipped, and every score derived
from it, is untouched. Records are marked `mbr_backfilled: true` and the dashboard
renders them as "would pick" rather than "selected", because presenting a
counterfactual as history is how a study talks itself into a result it did not earn.

    python tools/mbr_backfill.py            # all runs
    python tools/mbr_backfill.py --dry-run  # report, write nothing
    python tools/mbr_backfill.py --strip    # remove every backfilled field again
"""

import argparse
import glob
import json
import os.path as osp
import sys
from collections import defaultdict

sys.path.insert(0, osp.dirname(osp.dirname(osp.abspath(__file__))))

FIELDS = ("mbr_consensus", "mbr_picked", "mbr_distinct", "mbr_backfilled",
          "mbr_reason")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--strip", action="store_true", help="undo a previous backfill")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from src import config as c
    from src import evaluate as ev
    from src.tools import mbr

    records = sorted(glob.glob(osp.join(c.RESULTS, "dashboard_runs", "*.json")))
    if args.limit:
        records = records[:args.limit]

    if args.strip:
        n = 0
        for p in records:
            rec = json.load(open(p))
            hit = False
            for s in (rec.get("steps") or []):
                for f in FIELDS:
                    if f in s:
                        s.pop(f)
                        hit = True
            if hit:
                n += 1
                if not args.dry_run:
                    json.dump(rec, open(p, "w"), default=str)
        print(f"stripped MBR fields from {n} run records")
        return 0

    db = ev._db()
    work = osp.join(c.RESULTS, "mbr", "_backfill_work")
    n_runs = n_groups = n_acted = n_abstain = 0
    disagree = []

    try:
        for p in records:
            rid = osp.basename(p)[:-5]
            rec = json.load(open(p))
            steps = rec.get("steps") or []

            # Mirror the router's real candidate pool: `_revert_to_best` chooses
            # among CHECKPOINTS, and only an accepted or partial verdict becomes
            # one. Rejected and crashed attempts never reach the selector.
            #
            # This is not a detail. Feeding the selector every attempt instead
            # produced a group where MBR "picked" 0.007 over the 0.578 that
            # shipped — four near-identical rejected attempts out-voting the one
            # good answer. The real pipeline would never have seen them, so
            # reporting that as an MBR loss would have been an artefact of the
            # backfill, not a property of the method.
            by_sub = defaultdict(list)
            for s in steps:
                if (s.get("step") and osp.exists(s["step"])
                        and s.get("verdict") in ("accepted", "partial")):
                    by_sub[s.get("sub")].append(s)
            if not by_sub:
                continue

            # the state each sub-goal started from: the task input for the first,
            # then whatever the previous sub-goal kept
            try:
                _gt, start_rel = ev._gt_and_start(rid, db)
                task_input = osp.join(db.root_dir, start_rel.replace(".stl", ".step"))
            except Exception:
                continue
            kept = {}
            for s in steps:
                if s.get("verdict") in ("accepted", "partial") and s.get("step"):
                    kept[s.get("sub")] = s["step"]

            touched = False
            for sub in sorted(by_sub, key=lambda x: (x is None, x)):
                cands = sorted(by_sub[sub], key=lambda s: s.get("attempt") or 0)
                if len(cands) < 2:
                    continue
                n_groups += 1
                start = task_input if (sub is None or sub <= 0) else \
                    kept.get(sub - 1, task_input)
                try:
                    res = mbr.select(start, [s["step"] for s in cands],
                                     osp.join(work, rid, f"sub{sub}"))
                except Exception as e:
                    print(f"  {rid[:30]} sub{sub}: {type(e).__name__}: {e}")
                    continue

                abstained = bool(res.get("abstained"))
                if abstained:
                    n_abstain += 1
                else:
                    n_acted += 1
                cons_list = res.get("consensus") or [None] * len(cands)
                for s, cons in zip(cands, cons_list):
                    # A consensus score is only meaningful when the selector
                    # actually ran. On abstention `select` returns zeros, and
                    # writing those would render in the dashboard as "MBR 0.000"
                    # — indistinguishable from a candidate that genuinely agreed
                    # with nothing. Store None and the reason instead.
                    s["mbr_consensus"] = None if abstained else cons
                    s["mbr_reason"] = res.get("reason")
                    s["mbr_distinct"] = res.get("n_distinct")
                    s["mbr_backfilled"] = True
                    s["mbr_picked"] = False
                    touched = True
                if res.get("index") is not None:
                    pick = cands[res["index"]]
                    pick["mbr_picked"] = True
                    # did MBR disagree with what the run actually kept?
                    shipped = next((s for s in reversed(cands)
                                    if s.get("verdict") in ("accepted", "partial")),
                                   None)
                    if shipped is not None and shipped is not pick:
                        a = (pick.get("gt_scores") or {}).get("diff_f1")
                        b = (shipped.get("gt_scores") or {}).get("diff_f1")
                        disagree.append((rid, sub, shipped.get("attempt"), b,
                                         pick.get("attempt"), a))

            if touched:
                n_runs += 1
                if not args.dry_run:
                    json.dump(rec, open(p, "w"), default=str)
    finally:
        db.close_connection()

    print(f"runs updated        : {n_runs}{'  (dry run — nothing written)' if args.dry_run else ''}")
    print(f"groups examined     : {n_groups}  (acted {n_acted}, abstained {n_abstain})")
    print(f"groups where MBR disagrees with what shipped: {len(disagree)}")
    for rid, sub, sa, sg, pa, pg in disagree:
        d = (pg or 0) - (sg or 0)
        print(f"   {rid[:30]:30} sub{sub}: shipped try{sa} ({sg}) -> "
              f"MBR try{pa} ({pg})  delta {d:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
