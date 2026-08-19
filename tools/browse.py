#!/usr/bin/env python3
"""Browse neuralCAD-Edit requests: print the instruction, list every edit, open views.

Run from the repo root:
    cd sourcecode/IDETC26-Hackathon-Autodesk-neuralCAD-Edit
    PYTHONPATH=$(pwd) uv run python ../../tools/browse.py --list
    PYTHONPATH=$(pwd) uv run python ../../tools/browse.py <request_id>
    PYTHONPATH=$(pwd) uv run python ../../tools/browse.py <request_id> --open
    PYTHONPATH=$(pwd) uv run python ../../tools/browse.py <request_id> --topo
"""

import argparse
import os
import os.path as osp
import subprocess

from src.utils.db import DatabaseManager
from src.utils.process_config import load_config

CONFIG = "src/config/edit_192_external.json"


def geom(db, brep_id, ext):
    """Absolute path to <brep_id>.<ext>, or None if it was never produced."""
    if not brep_id:
        return None
    p = osp.join(db.root_dir, "breps", f"{brep_id}.{ext}")
    return p if osp.exists(p) else None


def topo(step_path):
    """One-line B-rep topology summary: face/edge counts, surface types, volume."""
    import cadquery as cq
    from collections import Counter

    s = cq.importers.importStep(step_path).val()
    types = dict(Counter(f.geomType() for f in s.Faces()))
    return (f"faces={len(s.Faces())} edges={len(s.Edges())} "
            f"vol={s.Volume():.2f}mm^3 {types}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("request_id", nargs="?", help="request _id (omit with --list)")
    ap.add_argument("--list", action="store_true", help="list all requests")
    ap.add_argument("--open", action="store_true", help="open iso views in Preview")
    ap.add_argument("--topo", action="store_true", help="dump B-rep topology per edit")
    args = ap.parse_args()

    db = DatabaseManager(load_config(CONFIG))

    if args.list or not args.request_id:
        for r in db.requests.find({}):
            print(f"{r['_id']:<45} {r.get('difficulty','?'):<7} {r.get('text','')[:70]}")
        db.close_connection()
        return

    req = db.requests.find_one({"_id": args.request_id})
    if not req:
        print(f"no such request: {args.request_id}")
        db.close_connection()
        return

    print(f"\nREQUEST    {req['_id']}")
    print(f"difficulty {req.get('difficulty')}")
    print(f"text       {req.get('text')}\n")

    start = req.get("brep_start")
    to_open = []

    rows = [("START (input)", start, "-")]
    for e in db.edits.find({"request": req["_id"]}):
        # the edit by the request's own author is the ground truth
        role = "GROUND TRUTH" if e["user"] == req["user"] else (
            "other human" if not e["user"].endswith("cadquery-script") else "model")
        rows.append((f"{role}: {e['user']}", e.get("brep_end"), role))

    for label, bid, _role in rows:
        stl, step, iso = geom(db, bid, "stl"), geom(db, bid, "step"), None
        if bid:
            cand = osp.join(db.root_dir, "breps", f"{bid}_toprightiso.jpg")
            iso = cand if osp.exists(cand) else None
        status = "ok" if stl else "NO GEOMETRY (failed edit -> scores 0.0)"
        print(f"  {label:<52} {status}")
        if step and args.topo:
            print(f"      {topo(step)}")
        if iso:
            to_open.append(iso)

    if args.open and to_open:
        subprocess.run(["open", *to_open])
        print(f"\nopened {len(to_open)} iso views")

    db.close_connection()


if __name__ == "__main__":
    main()
