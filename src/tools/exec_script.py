"""Subprocess entry point that executes one agent-generated CadQuery function.

Kept deliberately close to the repo's src/harnesses/cadquery_script.py contract
(a module defining `my_cad_function(args)`), so anything produced here is also
runnable by the official harness. Differences, all aimed at turning silent
failures into usable feedback:

  * validates the returned shape (non-null, isValid, non-zero volume) instead of
    exporting whatever comes back
  * prints a structured RESULT: line the parent parses
  * reports the full traceback on stdout so the executor agent can read it
"""

import argparse
import json
import os
import sys
import traceback

import cadquery as cq
from cadquery import exporters


def _invalid_solids(shape):
    """How many of a shape's solids fail isValid()."""
    try:
        return sum(0 if s.isValid() else 1 for s in shape.Solids())
    except Exception:
        return 0


def _invalid_solids_of(step_path):
    """Same count for the input file, or None if it cannot be read.

    Only called when the result is invalid, so the extra import costs nothing
    on the normal path.
    """
    if not step_path or not os.path.exists(str(step_path)):
        return None
    try:
        return _invalid_solids(cq.importers.importStep(str(step_path)).val())
    except Exception:
        return None


def run(function_src, input_file, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    g = {"cq": cq, "cadquery": cq, "Workplane": cq.Workplane,
         "Assembly": cq.Assembly, "exporters": exporters, "os": os, "sys": sys,
         "__builtins__": __builtins__}
    exec(function_src, g)
    if "my_cad_function" not in g:
        raise ValueError("no function named my_cad_function was defined")

    result = g["my_cad_function"]({"input_file": input_file,
                                   "output_dir": output_dir})
    if result is None:
        raise ValueError("my_cad_function returned None")

    # Unwrap to a real Shape. A Workplane whose `objects` list holds another
    # Workplane returns a Workplane from .val(), so one level is not enough.
    shape = result
    for _ in range(5):
        if isinstance(shape, cq.Assembly):
            shape = shape.toCompound()
            continue
        if hasattr(shape, "val"):
            inner = shape.val()
            if inner is shape:
                break
            shape = inner
            continue
        break
    if hasattr(shape, "val"):
        raise ValueError(
            "could not unwrap the return value to a Shape — it is still a "
            f"{type(shape).__name__} after 5 levels. Return the shape itself, "
            "not a Workplane wrapping another Workplane.")

    if shape is None or (hasattr(shape, "isNull") and shape.isNull()):
        raise ValueError("returned shape is null")

    # Validity is judged RELATIVE TO THE INPUT. Three of the 48 benchmark
    # inputs are already invalid B-reps (one bad solid out of 20, all from the
    # same source part), so demanding an absolutely valid result made those
    # requests unwinnable: every attempt was thrown away here and scored 0.0
    # however correct the edit was. What matters is that the edit did not break
    # anything that was intact — so the gate counts invalid solids instead.
    note = None
    if not shape.isValid():
        bad_out = _invalid_solids(shape)
        bad_in = _invalid_solids_of(input_file)
        if bad_in is None:
            raise ValueError("returned shape failed isValid() — invalid B-rep")
        if bad_out > bad_in:
            raise ValueError(
                f"returned shape failed isValid() — the edit broke geometry "
                f"that was intact: {bad_out} invalid solids out of "
                f"{len(shape.Solids())}, the input had {bad_in}")
        note = (f"result is not a valid B-rep, but neither was the input "
                f"({bad_out} invalid solids, input had {bad_in}) — accepted, "
                f"since the edit broke nothing that was intact")
        print("WARNING: " + note)

    vol = shape.Volume()
    if vol <= 1e-9:
        raise ValueError(f"returned shape has ~zero volume ({vol})")

    step_path = os.path.join(output_dir, "tmp.step")
    exporters.export(shape, step_path, exportType="STEP")
    if not os.path.exists(step_path):
        raise ValueError("STEP export produced no file")

    bb = shape.BoundingBox()
    out = {"ok": True, "step": step_path, "volume": round(vol, 4),
           "faces": len(shape.Faces()), "edges": len(shape.Edges()),
           "bbox": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)]}
    if note:
        out["validity_note"] = note
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--function_file", required=True)
    ap.add_argument("--input_file", default=None)
    ap.add_argument("--output_dir", required=True)
    a = ap.parse_args()

    with open(a.function_file) as f:
        src = f.read()

    try:
        info = run(src, a.input_file, a.output_dir)
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        info = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    print("RESULT: " + json.dumps(info))
    sys.exit(0 if info.get("ok") else 1)


if __name__ == "__main__":
    main()
