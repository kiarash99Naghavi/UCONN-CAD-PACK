#!/usr/bin/env python3
"""Tessellate every task's three B-reps into web-sized GLB files.

The site shows each edit as three live meshes side by side — the part that came
in, the part we shipped, and the part the human expert made — so all 48 tasks
need geometry a browser can load. The STL files under `outputs/` are what the
benchmark scored, not what a browser should download: they are OpenCASCADE's
fine tessellation, 642 MB across 96 files, up to 43 MB for a single part.

Decimating those meshes is the wrong fix. Every one of them was tessellated
from a B-rep that is still on disk, so this re-tessellates from the STEP
instead, at a deflection chosen for a 900 px viewport rather than for a voxel
metric. That keeps the flats flat and the fillets round at roughly 1/100th the
size, which no general-purpose mesh decimator would do.

Vertices are deliberately NOT merged across faces. OpenCASCADE triangulates
each B-rep face into its own node set, and keeping those islands separate is
what lets the viewer call `computeVertexNormals()` and get smooth shading
inside a fillet with a hard crease at every real edge. Merging first would
round the whole part off. Normals are therefore not written to the file at all.

Sources, per request id:
    input   the benchmark's `brep_start`   (dataset, CC BY-NC 4.0)
    gt      the expert edit's `brep_end`   (dataset, CC BY-NC 4.0)
    ours    `outputs/<rid>/ours.step`      (this repo)

Run it from this repo with the benchmark checkout's interpreter, which is the
one that has OCP and cadquery:

    NEURALCAD_REPO=/path/to/IDETC26-Hackathon-Autodesk-neuralCAD-Edit \
        $NEURALCAD_REPO/.venv/bin/python tools/make_web_meshes.py

Writes `site/assets/meshes/<request_id>/{input,ours,gt}.glb` and a
`site/data/meshes.json` index the page reads to know what exists.
"""

import json
import os
import os.path as osp
import struct
import sys

REPO = osp.dirname(osp.dirname(osp.abspath(__file__)))
OUT_DIR = osp.join(REPO, "site", "assets", "meshes")
INDEX = osp.join(REPO, "site", "data", "meshes.json")

# Deflection is set as a fraction of the part's own bounding-box diagonal, so a
# 4 mm bracket and a 1 m housing both come out with a comparable triangle
# budget. 1/400 is the coarsest setting where a 0.2 mm chamfer — the smallest
# feature any of the 48 instructions asks for — still reads as a chamfer.
LINEAR_DEFLECTION_REL = 1.0 / 400.0
ANGULAR_DEFLECTION = 0.35  # radians between adjacent facet normals

# One deflection does not fit all 48 parts: the coffee machine is an assembly of
# a few hundred solids and lands near 900k triangles at the setting that suits a
# bracket, which is an 11 MB page. Anything over budget is tessellated again,
# coarser, until it fits — so the page weight is bounded by the part's size on
# screen rather than by how many bodies happen to be in it.
TRIANGLE_BUDGET = 90_000
MAX_COARSEN_PASSES = 5
# Coarsening the linear deflection alone does not get there. Angular deflection
# puts a floor under the segment count of every curve — 0.35 rad is at least 18
# segments around any cylinder however small — and on an assembly with hundreds
# of little bosses and pins that floor *is* the triangle count. So both are
# relaxed together, with the angular term capped where a circle still reads as
# a circle rather than as a hexagon.
MAX_ANGULAR_DEFLECTION = 0.9


def benchmark_repo():
    """Path to the benchmark checkout that holds the dataset and its DB."""
    env = os.environ.get("NEURALCAD_REPO")
    if env:
        return osp.abspath(env)
    # the layout the submission ships in: this folder sits inside the benchmark
    guess = osp.dirname(osp.dirname(REPO))
    if osp.isdir(osp.join(guess, "src", "utils")):
        return guess
    sys.exit("set NEURALCAD_REPO to the benchmark checkout")


def load_db():
    root = benchmark_repo()
    sys.path.insert(0, root)
    from src.utils.db import DatabaseManager
    from src.utils.process_config import load_config

    cfg = osp.join(root, "src", "config", "edit_192_external.json")
    return DatabaseManager(load_config(cfg))


def tessellate(step_path):
    """STEP file -> (positions, indices) as flat Python lists.

    Returns positions as [x, y, z, x, y, z, ...] and indices as a flat list of
    triangle corners into it.
    """
    import cadquery as cq
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepTools import BRepTools

    shape = cq.importers.importStep(step_path).val()

    box = Bnd_Box()
    BRepBndLib.Add_s(shape.wrapped, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    diag = ((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) ** 0.5
    tol = max(diag * LINEAR_DEFLECTION_REL, 1e-6)

    ang = ANGULAR_DEFLECTION
    for _ in range(MAX_COARSEN_PASSES):
        # OpenCASCADE stores the triangulation on the shape and only ever
        # refines it, so a second, coarser request hands back the first mesh
        # unchanged. The stored one has to be thrown away for the loop to mean
        # anything.
        BRepTools.Clean_s(shape.wrapped)
        verts, tris = shape.tessellate(tol, ang)
        if len(tris) <= TRIANGLE_BUDGET or ang >= MAX_ANGULAR_DEFLECTION:
            break
        # triangle count falls roughly as 1/deflection^2 on a curved surface, so
        # this lands close to the budget in one step rather than halving blindly
        factor = (len(tris) / TRIANGLE_BUDGET) ** 0.5
        tol *= factor
        ang = min(ang * factor, MAX_ANGULAR_DEFLECTION)

    positions = []
    for v in verts:
        positions.extend((v.x, v.y, v.z))
    indices = []
    for t in tris:
        indices.extend(t)
    return positions, indices


def write_glb(path, positions, indices):
    """Write a single-mesh binary glTF with POSITION and indices only.

    No normals: the viewer derives them, which is both smaller on the wire and
    the only way to keep face islands shading independently (see module doc).
    """
    n_verts = len(positions) // 3
    # uint16 wherever it fits — that is most parts, and it halves the index
    # buffer, which is the larger of the two on a tessellated CAD solid
    if n_verts < 65536:
        idx_fmt, idx_comp, idx_size = "<%dH" % len(indices), 5123, 2
    else:
        idx_fmt, idx_comp, idx_size = "<%dI" % len(indices), 5125, 4

    idx_bytes = struct.pack(idx_fmt, *indices)
    idx_bytes += b"\x00" * (-len(idx_bytes) % 4)   # accessors must be 4-aligned
    pos_bytes = struct.pack("<%df" % len(positions), *positions)
    blob = idx_bytes + pos_bytes

    mins = [min(positions[i::3]) for i in range(3)]
    maxs = [max(positions[i::3]) for i in range(3)]

    gltf = {
        "asset": {"version": "2.0", "generator": "UCONN-CAD-PACK make_web_meshes"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 1}, "indices": 0}]}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0,
             "byteLength": len(idx_bytes), "target": 34963},
            {"buffer": 0, "byteOffset": len(idx_bytes),
             "byteLength": len(pos_bytes), "target": 34962},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": idx_comp,
             "count": len(indices), "type": "SCALAR"},
            {"bufferView": 1, "componentType": 5126, "count": n_verts,
             "type": "VEC3", "min": mins, "max": maxs},
        ],
    }

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode()
    json_bytes += b" " * (-len(json_bytes) % 4)    # chunks must be 4-aligned

    total = 12 + 8 + len(json_bytes) + 8 + len(blob)
    os.makedirs(osp.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack("<II", len(blob), 0x004E4942))
        f.write(blob)
    return len(indices) // 3, total


def main():
    db = load_db()
    root = db.root_dir
    requests = {r["_id"]: r for r in db.requests.find({})}

    # the expert edit for a request is the one whose author is the person who
    # filed it; anyone else editing the same part is the "second human" control
    gt_brep = {}
    for e in db.edits.find({}):
        req = requests.get(e.get("request"))
        if req is not None and e["user"] == req["user"]:
            gt_brep[e["request"]] = e.get("brep_end")

    with open(osp.join(REPO, "outputs", "manifest.json")) as f:
        manifest = json.load(f)

    def step_of(brep_id):
        if not brep_id:
            return None
        b = db.breps.find_one({"_id": brep_id})
        if not b or not b.get("step"):
            return None
        rel = b["step"][0] if isinstance(b["step"], list) else b["step"]
        p = osp.join(root, rel)
        return p if osp.exists(p) else None

    index, total_bytes, failures = {}, 0, []
    for i, rid in enumerate(sorted(manifest), 1):
        req = requests.get(rid, {})
        sources = {
            "input": step_of(req.get("brep_start")),
            "ours": osp.join(REPO, "outputs", rid, "ours.step"),
            "gt": step_of(gt_brep.get(rid)),
        }
        entry = {}
        for role, src in sources.items():
            if not src or not osp.exists(src):
                failures.append(f"{rid} {role}: no source")
                continue
            dst = osp.join(OUT_DIR, rid, role + ".glb")
            try:
                positions, indices = tessellate(src)
                tris, size = write_glb(dst, positions, indices)
            except Exception as exc:                      # noqa: BLE001
                failures.append(f"{rid} {role}: {type(exc).__name__}: {exc}")
                continue
            entry[role] = {"tris": tris, "bytes": size}
            total_bytes += size
        index[rid] = entry
        got = "".join(r[0] if r in entry else "-" for r in ("input", "ours", "gt"))
        print(f"[{i:2d}/{len(manifest)}] {rid[:28]:<28} {got}  "
              f"{sum(e['tris'] for e in entry.values()):>7,} tris", flush=True)

    os.makedirs(osp.dirname(INDEX), exist_ok=True)
    with open(INDEX, "w") as f:
        json.dump(index, f, indent=1, sort_keys=True)

    print(f"\n{len(index)} tasks, {total_bytes / 1e6:.1f} MB of GLB "
          f"-> {osp.relpath(OUT_DIR, REPO)}")
    if failures:
        print(f"{len(failures)} missing:")
        for line in failures:
            print("  " + line)


if __name__ == "__main__":
    main()
