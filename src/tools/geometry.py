"""Geometry inspection tools — the agent's "eyes" on the B-rep topology.

The baseline harness gives the model a STEP file and a single picture, then asks
it to write CadQuery selector strings blind. On an imported B-rep there is no
feature tree: a bracket is 266 anonymous faces. Guessing `.faces(">Z")` and
hoping is where baseline edits actually fail.

These tools turn the solid into an addressable, queryable index: every face and
edge with its type, size, position and radius, plus grouped summaries that
directly answer the questions instructions ask ("the hole edges", "the top
face", "all vertical surfaces").
"""

import os.path as osp
from collections import Counter, defaultdict

import cadquery as cq


def _fmt(v, n=3):
    return round(float(v), n)


def _xyz(p, n=3):
    return [_fmt(p.x, n), _fmt(p.y, n), _fmt(p.z, n)]


def _circle_radius(edge):
    """Radius of a circular edge, or None."""
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_CurveType

        ad = BRepAdaptor_Curve(edge.wrapped)
        if ad.GetType() == GeomAbs_CurveType.GeomAbs_Circle:
            return _fmt(ad.Circle().Radius(), 4)
    except Exception:
        pass
    return None


def _circle_center(edge):
    """TRUE center of a circular edge's circle, as [x, y, z], or None.

    `Edge.Center()` is the arc's CENTER OF MASS — identical for a full
    circle, but up to ~r off the circle center for a partial arc (measured
    1.1 mm on an r=1.4 lug-hole arc). An index built on centroids sends every
    "match by measured position" selection to a point where no circle is, so
    circular edges are indexed by the curve's own center instead.
    """
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_CurveType

        ad = BRepAdaptor_Curve(edge.wrapped)
        if ad.GetType() == GeomAbs_CurveType.GeomAbs_Circle:
            loc = ad.Circle().Location()
            return [_fmt(loc.X()), _fmt(loc.Y()), _fmt(loc.Z())]
    except Exception:
        pass
    return None


def _cylinder_radius(face):
    """Radius of a cylindrical face, or None."""
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_SurfaceType

        ad = BRepAdaptor_Surface(face.wrapped)
        if ad.GetType() == GeomAbs_SurfaceType.GeomAbs_Cylinder:
            return _fmt(ad.Cylinder().Radius(), 4)
    except Exception:
        pass
    return None


def _cylinder_sweep_deg(face):
    """How far a cylindrical face wraps around its axis, in degrees, or None.

    The one number that separates a HOLE WALL from a CORNER FILLET, and the
    index had no way to say it. A bore wraps 360 (or ~180 per half for a slot
    end-wall); a rounded corner sweeps ~90. Read straight off the surface's
    U parameter range, so it costs nothing.

    Measured: four r=6 faces that were the 90-degree corner blends of a boss
    were read by the planner as "the slot's end-wall cylinders", and an entire
    run — six attempts, two replans — was spent trying to cut a through-slot
    at a slot that does not exist, while the part's actual blind slot sat
    elsewhere in the same index.
    """
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_SurfaceType
        import math

        ad = BRepAdaptor_Surface(face.wrapped)
        if ad.GetType() != GeomAbs_SurfaceType.GeomAbs_Cylinder:
            return None
        sweep = abs(ad.LastUParameter() - ad.FirstUParameter())
        return _fmt(math.degrees(sweep), 1)
    except Exception:
        return None


def _cylinder_axis(face):
    """Unit axis direction of a cylindrical face, or None.

    Sign-normalized (largest-magnitude component made positive) so two mouths
    of the same bore report the same line direction. Without this the index
    names a bore only by radius and center, and its orientation is invisible
    to the planner — which then supplies a direction of its own, usually a
    world axis, and the whole run builds and verifies against that guess.
    """
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_SurfaceType

        ad = BRepAdaptor_Surface(face.wrapped)
        if ad.GetType() != GeomAbs_SurfaceType.GeomAbs_Cylinder:
            return None
        d = ad.Cylinder().Axis().Direction()
        v = [d.X(), d.Y(), d.Z()]
        lead = max(v, key=abs)
        if lead < 0:
            v = [-c for c in v]
        return [_fmt(c, 3) for c in v]
    except Exception:
        pass
    return None


def load_shape(step_path):
    return cq.importers.importStep(str(step_path)).val()


def _safe(fn, digits=3):
    """OCC mass properties crash outright on degenerate compounds (an empty
    child raises StopIteration deep in computeMass). 3 of the 48 benchmark
    inputs are such compounds; None beats killing the whole run in _prepare."""
    try:
        return _fmt(fn(), digits)
    except Exception:
        return None


def _summarize_shape(s, name):
    """The body of `summarize`, on an already-loaded shape.

    Split out so a caller that needs the shape itself as well (compare(), which
    also keys its faces) can load it once instead of twice. `summarize`'s own
    signature and return shape are unchanged — other callers depend on them.
    """
    bb = s.BoundingBox()
    kinds = defaultdict(int)
    for f in s.Faces():
        kinds[f.geomType()] += 1
    return {
        "file": name,
        "valid": bool(s.isValid()),
        "solids": len(s.Solids()),
        "faces": len(s.Faces()),
        "edges": len(s.Edges()),
        "vertices": len(s.Vertices()),
        "surface_types": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "volume_mm3": _safe(s.Volume),
        "area_mm2": _safe(s.Area),
        "bbox_size": [_fmt(bb.xlen), _fmt(bb.ylen), _fmt(bb.zlen)],
        "bbox_min": [_fmt(bb.xmin), _fmt(bb.ymin), _fmt(bb.zmin)],
        "bbox_max": [_fmt(bb.xmax), _fmt(bb.ymax), _fmt(bb.zmax)],
    }


def summarize(step_path):
    """High-level shape summary: counts, surface mix, bbox, volume."""
    return _summarize_shape(load_shape(step_path), osp.basename(str(step_path)))


# A radius floor proportional to the PART is what emptied the index. On the
# 331x43x381 mm rotor the bbox diagonal is 507 mm, so a 2%-of-diagonal floor
# sits at 10.14 mm — above every hole in the part (r=3.175 x11, r=7.62 x16,
# r=6.35 x8 ...). The executor was then handed a section reading "none within
# feature-size range" and told to "use measured numbers from the geometry
# index", with no numbers in it. Measured across the stored runs, 19 of 73
# attempts ran cleanly and selected nothing.
#
# Blend artifacts are small in ABSOLUTE terms — the sample bracket carries 76
# circular edges at r=0.02 — so the floor that removes them is absolute, and
# the part-relative number is demoted from a filter to a *label*: families
# below it are still listed, just after the feature-scale ones.
#
# That demotion was applied to CYLINDRICAL faces but not, until now, to
# circular edges, which went on hard-filtering `floor <= r <= ceil`. On the
# 331x43x381 mm rotor the floor evaluates to 1.014 mm, so every circular edge
# under ~1 mm — the chamfer and fillet rings the instructions most often name —
# was deleted from the index before the executor ever saw it. The bands are now
# ORDERING AND LABELS ONLY, on both. Nothing is dropped for being the wrong
# size: an index that hides the target entity turns "select by measured numbers
# from the geometry index" into an instruction the model cannot follow, and the
# model's only remaining move is to invent a radius that matches no edge.
_BLEND_RADIUS_MM = 0.05         # below this it is intersection noise anywhere
_BLEND_RADIUS_FRAC = 0.002      # ... or 0.2% of the diagonal, whichever is larger
_FEATURE_RADIUS_FRAC = 0.01     # at/above 1% of the diagonal reads as a feature

# How many members of one family are STORED. The old value was 8, which on the
# rotor's 32-edge r=7.62 family hid three quarters of the holes; a selector
# built from the visible 8 chamfers 8 of 32 edges and reads as a partial edit.
# 24 covers every family in the benchmark set outright.
_FAMILY_MEMBERS_STORED = 24

# One phrasing for every truncation in this module: "showing N of M". A list
# that fits prints bare. A list that does not says how much it is not showing,
# so the reader can always tell "there are only 4 of these" from "4 of 22 were
# printed" — the distinction the agent cannot recover any other way.
# At or below this sweep a cylindrical face is a rounded corner, not a bore
# wall. Corner blends are modelled as quarter-cylinders; a slot end-wall is a
# half (180) and a hole a full turn.
_BLEND_SWEEP_DEG = 100.0

_PRINT_SOLIDS = 24              # per-body rows before the table is capped
_PRINT_GENERIC_FACES = 60       # rows in the all-surface-types table
_PRINT_INNER = 24               # faces listed as carrying inner wires
_PRINT_AXIAL = 20               # bore/port features (paired ring mouths)
_PRINT_FAMILIES = 40            # circular-edge / cylindrical-face families
_PRINT_CENTERS = 6              # per-family coordinate triples (verbose)
# A family small enough to print WHOLE always is. Truncation is a token
# trade-off that only pays on a big family; on a small one it saves a few
# characters and costs the reader the feature. Measured: a 4-member port
# family printed as "showing 2 of 4" hid the far end of each port tube — the
# planner read a port as a single circle instead of a 38 mm bore, placed a new
# one by flipping a coordinate, and the run scored 0.
_FAMILY_PRINT_WHOLE_UPTO = 8
_PRINT_IDX = 16                 # per-family face_idx / edge_idx (cheap)
_PRINT_PLANAR = 40              # planar faces
_PRINT_SOLIDS = 24              # per-solid rows in the interference section
_PRINT_PAIRS = 12               # measured overlapping pairs (largest first)


def _radius_bands(diag):
    """(floor, feature_scale, ceil) radii for a part of this size."""
    return (max(_BLEND_RADIUS_MM, diag * _BLEND_RADIUS_FRAC),
            diag * _FEATURE_RADIUS_FRAC,
            diag * 0.25)


# Ordering rank and label for a radius, given this part's bands. Feature-scale
# families first (the usual targets), then blend-scale, then the two extremes.
_SCALE_LEGEND = {
    "feature": "at or above 1% of the part diagonal — reads as a real feature",
    "small": "below feature scale but above the absolute noise floor — usually "
             "a blend, but on a large part real fastener holes land here",
    "blend": "below the noise floor — usually a fillet/chamfer intersection "
             "artifact, and also exactly where a small chamfer ring lives",
    "oversize": "radius above a quarter of the part diagonal — normally a "
                "sweeping boundary curve, not a hole",
}


def _radius_scale(r, floor, feature, ceil):
    """(sort_rank, label) — never a keep/drop decision, see the note above."""
    if r > ceil:
        return (3, "oversize")
    if r >= feature:
        return (0, "feature")
    if r >= floor:
        return (1, "small")
    return (2, "blend")


def circular_edge_groups(step_path, top=None):
    """Circular edges grouped by radius — the hole / fillet families.

    The single most useful query for these instructions: "add a chamfer to the
    hole edges" becomes "the 8 circular edges of radius 1.3".

    EVERY radius family is returned. Real parts carry circular edges that are
    not holes at both extremes — fillet-blend intersection artifacts below,
    long sweeping boundary curves whose radius exceeds the part itself above
    (r=15 on a 13x5x8 mm bracket) — and those are pushed to the BOTTOM of the
    list and labelled, not deleted. A family the model cannot see is a family
    it will invent coordinates for.

    `top` caps the number of families returned; None (the default) means no
    cap. When it does cut, the note states the true total.
    """
    s = load_shape(step_path)
    bb = s.BoundingBox()
    diag = (bb.xlen ** 2 + bb.ylen ** 2 + bb.zlen ** 2) ** 0.5
    floor, feature, ceil = _radius_bands(diag)

    groups = defaultdict(list)
    for i, e in enumerate(s.Edges()):
        r = _circle_radius(e)
        if r is None:
            continue
        # True circle center, not the arc centroid — see _circle_center.
        c = _circle_center(e) or _xyz(e.Center())
        length = _fmt(e.Length())
        # A partial arc behaves differently from a full circle in almost every
        # downstream operation (chamfer cutters must be clipped to its span,
        # its centroid lies off-center), so the index says which it is.
        import math as _math
        partial = e.Length() < 0.98 * 2 * _math.pi * float(r)
        groups[r].append({"edge_idx": i, "center": c, "length": length,
                          "partial_arc": partial})

    # No radius filter. Bands order and label; within a band the repeated
    # families come first — hole families repeat (4 holes -> 8 circular edges,
    # top and bottom), incidental curves do not.
    ranked = [(_radius_scale(r, floor, feature, ceil), r, v)
              for r, v in groups.items()]
    ranked.sort(key=lambda t: (t[0][0], -len(t[2]), -t[1]))

    kept = ranked if top is None else ranked[:top]
    out = []
    for (_, label), r, items in kept:
        out.append({
            "radius": r,
            "diameter": _fmt(r * 2, 4),
            "count": len(items),
            "scale": label,
            "centers": [it["center"] for it in items[:_FAMILY_MEMBERS_STORED]],
            "edge_idx": [it["edge_idx"] for it in items[:_FAMILY_MEMBERS_STORED]],
            "partial_arcs": sum(1 for it in items if it.get("partial_arc")),
        })
    shown, total = len(out), len(groups)
    if total == 0:
        # An honest empty: the solid genuinely has no circular edge. Said
        # plainly so it is never mistaken for a section that was filtered away.
        note = "this solid has no circular edges at all — nothing was filtered"
    elif shown == total:
        note = (f"all {total} circular-edge radius families listed — no family "
                f"is filtered out by size")
    else:
        note = (f"showing {shown} of {total} circular-edge radius families, "
                f"feature-scale first")
    labels = [k for k in ("feature", "small", "blend", "oversize")
              if any(g["scale"] == k for g in out)]
    if labels != ["feature"]:
        note += (f". [scale] orders and labels only, it does not filter: "
                 + "; ".join(f"'{k}' {_SCALE_LEGEND[k]}" for k in labels)
                 + f" (feature scale = {_fmt(feature,3)} mm, noise floor = "
                   f"{_fmt(floor,3)} mm on this {_fmt(diag,1)} mm diagonal)")
    return {"groups": out, "note": note, "feature_radius": _fmt(feature, 3)}


def cylindrical_face_groups(step_path):
    """Cylindrical faces grouped by radius — the hole walls / bosses.

    Every family is returned, tagged with the same feature/small band as the
    circular edges. Filtering happens (if at all) at render time, where the
    instruction's own sizes are available to override it.
    """
    s = load_shape(step_path)
    bb = s.BoundingBox()
    diag = (bb.xlen ** 2 + bb.ylen ** 2 + bb.zlen ** 2) ** 0.5
    floor, feature, ceil = _radius_bands(diag)
    groups = defaultdict(list)
    for i, f in enumerate(s.Faces()):
        r = _cylinder_radius(f)
        if r is None:
            continue
        c = f.Center()
        groups[r].append({"face_idx": i, "center": _xyz(c), "area": _fmt(f.Area()),
                          "axis": _cylinder_axis(f),
                          "sweep_deg": _cylinder_sweep_deg(f)})
    rows = []
    for r, v in sorted(groups.items()):
        rank, label = _radius_scale(r, floor, feature, ceil)
        rows.append({"radius": r, "diameter": _fmt(r * 2, 4), "count": len(v),
                     "scale": label, "_rank": rank, "faces": v})
    # Feature-scale first so any truncation downstream cuts the least useful
    # families, never the real hole walls.
    rows.sort(key=lambda g: (g["_rank"], -g["count"]))
    for g in rows:
        g.pop("_rank")
    return rows


def planar_faces(step_path, limit=None):
    """Planar faces with outward normals, largest first.

    Answers "the top face", "all vertical surfaces", "the mounting face".

    `limit=None` (the default) collects EVERY planar face. It used to default
    to 30 and `inspect` passed 12, so on the 45-plane rotor two thirds of the
    part's flat surfaces never reached the index and the executor had no
    measured centre to select a mounting face by. Truncation now happens once,
    at render time in `to_prompt`, where the true total can be printed with it.
    The parameter is kept for callers that want a cap of their own.
    """
    s = load_shape(step_path)
    rows = []
    for i, f in enumerate(s.Faces()):
        if f.geomType() != "PLANE":
            continue
        c = f.Center()
        row = {"face_idx": i, "area": _fmt(f.Area()), "center": _xyz(c)}
        try:
            n = f.normalAt()
            row["normal"] = _xyz(n)
            ax = max(("x", abs(n.x)), ("y", abs(n.y)), ("z", abs(n.z)), key=lambda t: t[1])
            row["axis"] = ax[0] if ax[1] > 0.95 else "oblique"
            row["vertical"] = abs(n.z) < 0.05
        except Exception:
            pass
        rows.append(row)
    rows.sort(key=lambda r: -r["area"])
    return rows if limit is None else rows[:limit]


def face_adjacency(step_path):
    """[[i, j], ...] — index pairs of faces that share at least one edge.

    Indices are `enumerate(shape.Faces())`, the same enumeration every other
    face_idx in this module uses. Consumed by `color_plan` so touching faces
    can be given different shades. [] on any failure — adjacency is a
    nice-to-have, never worth killing an inspection over.
    """
    try:
        s = load_shape(step_path)
        owners = defaultdict(list)
        for i, f in enumerate(s.Faces()):
            for e in f.Edges():
                owners[e.hashCode()].append(i)
        pairs = set()
        for ids in owners.values():
            pairs.update((a, b) for a in ids for b in ids if a < b)
        return sorted(list(p) for p in pairs)
    except Exception:
        return []


def solid_interference(step_path, max_pairs=60):
    """Per-solid identity plus MEASURED colliding pairs, for multi-body files.

    Born on the coffeepot collision task: the instruction said "remove the
    collision between handle and cofeepot", nothing in the index said which of
    the 20 solids actually collide, and six attempts cut solids that measured
    28 mm apart (their intersection printed 0.0) while the real overlap — a
    2695 mm^3 pocket at x=-92 — was never touched. This table replaces that
    guess with a measurement.

    Returns None for single-solid files or on any failure; capped at
    `max_pairs` boolean-common computations (bbox-overlapping pairs only).
    """
    try:
        s = load_shape(step_path)
        sols = s.Solids()
        if len(sols) < 2:
            return None
        rows = []
        for i, so in enumerate(sols):
            bb = so.BoundingBox()
            rows.append({"solid_idx": i, "volume": _fmt(so.Volume()),
                         "center": _xyz(so.Center()),
                         "bbox_min": [_fmt(v) for v in (bb.xmin, bb.ymin, bb.zmin)],
                         "bbox_max": [_fmt(v) for v in (bb.xmax, bb.ymax, bb.zmax)]})

        def _overlap(a, b):
            return (a.xmin <= b.xmax and b.xmin <= a.xmax
                    and a.ymin <= b.ymax and b.ymin <= a.ymax
                    and a.zmin <= b.zmax and b.zmin <= a.zmax)

        boxes = [so.BoundingBox() for so in sols]
        pairs, checked, truncated = [], 0, False
        for i in range(len(sols)):
            for j in range(i + 1, len(sols)):
                if not _overlap(boxes[i], boxes[j]):
                    continue
                if checked >= max_pairs:
                    truncated = True
                    break
                checked += 1
                try:
                    common = sols[i].intersect(sols[j])
                    v = abs(common.Volume())
                except Exception:
                    continue
                if v > 1e-3:
                    pairs.append({"a": i, "b": j, "volume": _fmt(v),
                                  "center": _xyz(common.Center())})
            if truncated:
                break
        pairs.sort(key=lambda p: -float(p["volume"]))
        return {"solids": rows, "pairs": pairs, "truncated": truncated}
    except Exception:
        return None


def _shape_key(x):
    """Identity of one face/edge, for ownership lookups.

    `hash(TopoDS_Shape)` covers the TShape AND the Location, so two instances
    of the same underlying surface placed at different positions — how repeated
    bodies in an assembly are stored — stay distinct. A TShape-only key looks
    equivalent on this dataset (verified: no collisions even on the 56-body
    part) but would silently attribute every instance to whichever body was
    walked first. Two wrappers over the SAME face, one from `shape.Faces()` and
    one from `solid.Faces()`, still hash equal, which is what ownership needs.
    """
    return hash(x.wrapped)


def solid_ownership(step_path, _shape=None):
    """Which solid owns each face and each edge — (rows, face_owner, edge_owner).

    `face_owner[i]` is the index of the solid owning `shape.Faces()[i]`, in the
    same global ordering every other section of this index uses; likewise for
    edges. `rows` is one summary record per solid.

    Half of this benchmark's parts (24 of 48) carry more than one solid, and
    until now no row in this index said which body an entity belonged to. The
    planner therefore had to guess, and a sub-goal that names a face on one
    body and edges on another is unsatisfiable: the executor cannot select
    both from one solid, and every attempt it spends trying is lost. Measured
    on the scroll-wheel part — the plan cited "solid #0" with edge_idx
    [25,26,27,29], which belong to solid #1; four of five attempts died on it.
    """
    s = _shape if _shape is not None else load_shape(step_path)
    faces, edges = s.Faces(), s.Edges()
    fown, eown = {}, {}
    rows = []
    for si, sol in enumerate(s.Solids()):
        sf, se = sol.Faces(), sol.Edges()
        for f in sf:
            fown.setdefault(_shape_key(f), si)
        for e in se:
            eown.setdefault(_shape_key(e), si)
        bb = sol.BoundingBox()
        rows.append({
            "solid": si,
            "volume": _safe(sol.Volume),
            "faces": len(sf),
            "edges": len(se),
            "bbox_min": [_fmt(bb.xmin), _fmt(bb.ymin), _fmt(bb.zmin)],
            "bbox_max": [_fmt(bb.xmax), _fmt(bb.ymax), _fmt(bb.zmax)],
        })
    face_owner = [fown.get(_shape_key(f)) for f in faces]
    edge_owner = [eown.get(_shape_key(e)) for e in edges]

    # A hash key is an identity claim, and a wrong one here mislabels a body
    # silently — the exact failure this whole section exists to remove. If the
    # keys are not unique per entity, fall back to topological identity
    # (`isSame`), which is O(n*m) but only ever runs on a part that broke the
    # fast path.
    if len(set(_shape_key(f) for f in faces)) != len(faces) or \
            len(set(_shape_key(e) for e in edges)) != len(edges):
        solids_list = s.Solids()
        face_owner = [_owner_by_identity(f, solids_list, "Faces") for f in faces]
        edge_owner = [_owner_by_identity(e, solids_list, "Edges") for e in edges]
    return rows, face_owner, edge_owner


def _owner_by_identity(entity, solids, kind):
    """Which solid contains `entity`, compared with `isSame`. Slow fallback."""
    for si, sol in enumerate(solids):
        for other in getattr(sol, kind)():
            try:
                if entity.wrapped.IsSame(other.wrapped):
                    return si
            except Exception:
                continue
    return None


def _point_in_solid(shape, p):
    """Is `p` inside the material? False on any classifier failure."""
    try:
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.gp import gp_Pnt
        from OCP.TopAbs import TopAbs_State
        cl = BRepClass3d_SolidClassifier(shape.wrapped)
        cl.Perform(gp_Pnt(float(p[0]), float(p[1]), float(p[2])), 1e-6)
        return cl.State() == TopAbs_State.TopAbs_IN
    except Exception:
        return False


def pocket_depth_state(insp, step_path=None, _shape=None):
    """Which openings are BLIND pockets and which already go THROUGH.

    Instructions discriminate by predicate, not by coordinate: "change THAT
    vertical slot to cut through", "the long edge that does NOT have a radius",
    "the flat end (the one without fillet)". The index lists every candidate
    but not the property the sentence selects on, so the planner picks by
    position with a 1-in-N chance. Measured: a part with two congruent slots —
    one blind, one already through — got the wrong one, cut 748 voxels 120 mm
    away from the ground truth's 2028, and scored 0.000 where a single-shot
    baseline scored 1.000.

    Two openings are the two MOUTHS of one through-feature when their loops
    have the same size and shape and are offset along exactly one axis. The
    comparison is on the LOOP's own bounding box, not the host face's area —
    the two mouths sit on different faces, which are generally different sizes.

    Returns {face_idx: "through" | "blind"}.
    """
    inner = insp.get("inner_wires") or []
    if not inner:
        return {}
    s = _shape if _shape is not None else load_shape(step_path)
    edges = s.Edges()

    def loop_box(row):
        pts = []
        for j in row.get("inner_edges") or []:
            if j < len(edges):
                bb = edges[j].BoundingBox()
                pts += [(bb.xmin, bb.ymin, bb.zmin), (bb.xmax, bb.ymax, bb.zmax)]
        if not pts:
            return None
        lo = [min(p[k] for p in pts) for k in range(3)]
        hi = [max(p[k] for p in pts) for k in range(3)]
        return lo, hi

    boxes = {}
    for row in inner:
        # A face carrying SEVERAL inner loops cannot be judged this way. The
        # row's `inner_edges` is the per-loop groups flattened into one list
        # (see the `inner_rows` construction), so `loop_box` returns the
        # bounding box of ALL the loops together — not "the LOOP's own
        # bounding box" this function compares on. Two faces whose hole
        # PATTERNS happen to span different extents then fail the match and
        # both get stamped `blind`, and the state is per-face anyway, so a
        # face whose loops genuinely differ has no single right answer.
        #
        # Measured on task 3 of batch 2 (3YH2WFSRM22W7DKT_1769177020): a plate
        # with 7 through-holes, host faces slightly tapered (normals
        # [0.035,0,0.999] and [0.042,0,-0.999]), so the two aggregates differed
        # by more than the 5% tolerance and BOTH mouths were printed [BLIND].
        # QA then read the run's volume shortfall as "the holes are not fully
        # through" — a hypothesis the index itself appeared to corroborate,
        # while the executor's own probe showed every hole exactly Ø5.000 and
        # clean through. Saying nothing is strictly better than saying
        # something false; the label is simply omitted when absent.
        if (row.get("n_inner") or 1) > 1:
            continue
        b = loop_box(row)
        if b:
            boxes[row["face_idx"]] = (b, row.get("solid"))

    out = {}
    for fi, ((lo, hi), owner) in boxes.items():
        size = [hi[k] - lo[k] for k in range(3)]
        through = False
        for fj, ((lo2, hi2), owner2) in boxes.items():
            if fj == fi or owner2 != owner:
                continue
            size2 = [hi2[k] - lo2[k] for k in range(3)]
            # same loop footprint ...
            if any(abs(size[k] - size2[k]) > 0.05 * max(size[k], size2[k], 1e-9)
                   for k in range(3)):
                continue
            # ... offset along exactly one axis (the feature's own direction)
            moved = [k for k in range(3) if abs(lo2[k] - lo[k]) > 1e-3]
            if len(moved) != 1:
                continue
            # Aligned mouths are NOT proof of a through-feature: two blind
            # pockets sunk from opposite faces look identical from outside.
            # The question is whether MATERIAL sits between them, which only
            # the solid itself can answer. Measured on a bracket whose handle
            # carries exactly that arrangement — a 22 mm web between two
            # pockets — mouth geometry alone called it "through", which would
            # tell the planner to skip the one feature the instruction asks it
            # to open up.
            mid = [(lo[k] + hi[k] + lo2[k] + hi2[k]) / 4.0 for k in range(3)]
            if _point_in_solid(s, mid):
                continue        # solid between the mouths -> two blind pockets
            through = True
            break
        out[fi] = "through" if through else "blind"
    return out


def axial_features(groups, tol=0.05):
    """Coaxial rings of one radius collapsed into the FEATURES they bound.

    A bore, port or boss shows up in the circular-edge table as two rings —
    one per mouth — and a through-hole in a plate likewise. Printed as loose
    centres they read as separate holes, and the planner then treats a tube as
    a circle: it never learns how far the feature runs, or that the two
    coordinates it is looking at are the two ENDS of one thing.

    Measured on a radiator: r=25.4 has four centres that are two ports, each
    spanning x -107.95..-69.85. The plan read them as four independent points,
    placed a "new" port by flipping one coordinate onto a spot that already
    had one, and the run scored 0.

    Returns [{radius, axis, n, span, length, at, edge_idx}] — `at` is the
    fixed cross-axis position (None in the axis slot) and `span` the extent
    along `axis`.
    """
    out = []
    for g in groups or []:
        ctrs = g.get("centers") or []
        ids = g.get("edge_idx") or []
        if len(ctrs) < 2:
            continue
        # Which axis do these centres run along? The one whose value varies
        # while the other two hold still for at least one pair of rings.
        best = None
        for ax in range(3):
            keys = {}
            for k, c in enumerate(ctrs):
                key = tuple(round(c[j], 3) for j in range(3) if j != ax)
                keys.setdefault(key, []).append(k)
            paired = sum(len(v) for v in keys.values() if len(v) > 1)
            if paired and (best is None or paired > best[0]):
                best = (paired, ax, keys)
        if not best:
            continue
        _, ax, keys = best
        for key, members in sorted(keys.items()):
            if len(members) < 2:
                continue
            vals = [ctrs[k][ax] for k in members]
            # Zero extent means these rings all sit at the same station along
            # the chosen axis: that is a PATTERN of holes in one plane, not a
            # tube with two mouths, and calling it a bore would invent a
            # feature. The circular-edge table already lists them.
            if max(vals) - min(vals) <= tol:
                continue
            at = list(key)
            at.insert(ax, None)
            out.append({
                "radius": g.get("radius"),
                "axis": "XYZ"[ax],
                "n": len(members),
                "span": [_fmt(min(vals)), _fmt(max(vals))],
                "length": _fmt(max(vals) - min(vals)),
                "at": at,
                "edge_idx": [ids[k] for k in members if k < len(ids)],
            })
    return out



def face_table(step_path, _shape=None, face_owner=None):
    """(all_face_rows, inner_loop_rows) — every face, and every opening.

    Two answers the index could not give before, computed in ONE pass because
    `Face.Wires()` is the expensive call and both need it:

    - EVERY surface type. The specialised sections cover circles, cylinders
      and planes: across this benchmark's 48 inputs that is 62% of faces and
      27% of edges. Splines, tori, cones and spheres were simply absent while
      the prompt claimed the index was complete, so a target living on one
      could not be named and the planner substituted whatever it could see —
      frequently on the wrong body.
    - INNER BOUNDARY LOOPS. A face's outer wire is its silhouette; any further
      wire is an opening cut through it. "Fillet the slot rim" resolves to
      exactly those edges. Attempts used to die reporting "top face has no
      inner wires" while the real opening sat on a spline face the index never
      printed.
    """
    s = _shape if _shape is not None else load_shape(step_path)
    # Edge identity -> global edge index, once, so each face costs only its own
    # wires instead of a scan over every edge in the part.
    edge_id = {_shape_key(e): j for j, e in enumerate(s.Edges())}
    rows, inner_rows = [], []
    for i, f in enumerate(s.Faces()):
        try:
            owner = face_owner[i] if face_owner is not None else None
            wires = f.Wires()
            c = f.Center()
            row = {"face_idx": i, "type": f.geomType(), "area": _fmt(f.Area()),
                   "center": _xyz(c), "n_wires": len(wires), "solid": owner}
            if row["type"] == "PLANE":
                try:
                    row["normal"] = _xyz(f.normalAt())
                except Exception:
                    pass
            rows.append(row)

            if len(wires) < 2:
                continue
            okey = {_shape_key(e) for e in f.outerWire().Edges()}
            inner = []
            for w in wires:
                ids = [edge_id[k] for k in
                       (_shape_key(e) for e in w.Edges())
                       if k not in okey and k in edge_id]
                if ids:
                    inner.append(ids)
            if inner:
                inner_rows.append({
                    "face_idx": i, "type": row["type"], "area": row["area"],
                    "center": row["center"], "n_inner": len(inner),
                    "inner_edges": [x for grp in inner for x in grp],
                    "solid": owner,
                })
        except Exception:
            continue
    return rows, inner_rows


def inspect(step_path, top_edges=None, top_faces=None):
    """Full inspection bundle handed to the agents.

    Every circular-edge family, cylindrical-face family and planar face is in
    it, PLUS a generic per-face table covering the surface types those three
    sections cannot express, the inner-wire (opening) structure, and the solid
    that owns each entity. `top_edges`/`top_faces` default to None (no cap) and
    are kept only for callers that want one; when they cut, the caller loses
    the totals, which is why nothing here uses them. Existing dict keys are
    unchanged — other modules read them.
    """
    shape = load_shape(step_path)
    solids, face_owner, edge_owner = solid_ownership(step_path, _shape=shape)
    all_rows, inner_rows = face_table(step_path, _shape=shape,
                                      face_owner=face_owner)
    cyl = cylindrical_face_groups(step_path)
    plan = planar_faces(step_path)

    # Ownership is stamped onto the rows the agents actually read, so a
    # sub-goal can cite tags and their body in the same breath.
    for row in plan:
        row["solid"] = _owner_of(face_owner, row.get("face_idx"))
    for g in cyl:
        for f in g.get("faces", []):
            f["solid"] = _owner_of(face_owner, f.get("face_idx"))
    circ = circular_edge_groups(step_path, top=top_edges)
    for g in circ.get("groups", []):
        g["solids"] = sorted({o for o in
                              (_owner_of(edge_owner, j)
                               for j in g.get("edge_idx", []))
                              if o is not None})

    return {
        "summary": summarize(step_path),
        "solids": solids,
        "face_owner": face_owner,
        "edge_owner": edge_owner,
        "circular_edges": circ,
        "cylindrical_faces": cyl if top_faces is None else cyl[:top_faces],
        "planar_faces": plan if top_faces is None else plan[:top_faces],
        "all_faces": all_rows,
        "inner_wires": inner_rows,
        "axial_features": axial_features(circ.get("groups")),
        "pocket_state": pocket_depth_state({"inner_wires": inner_rows},
                                           _shape=shape),
        "face_adjacency": face_adjacency(step_path),
        "interference": solid_interference(step_path),
    }


def _owner_of(owner_list, idx):
    if owner_list is None or idx is None or idx >= len(owner_list):
        return None
    return owner_list[idx]


def _capped(values, cap, total=None):
    """A list rendered up to `cap` entries, never SILENTLY cut.

    `total` is the true population when `values` is itself already a stored
    sample (families keep `_FAMILY_MEMBERS_STORED` members but know their real
    `count`). A list that fits prints bare; one that does not carries
    "(showing N of M)" — the one phrasing used for every truncation here.
    """
    total = len(values) if total is None else total
    shown = list(values[:cap])
    txt = "[" + ", ".join(str(v) for v in shown) + "]"
    if total > len(shown):
        txt += f" (showing {len(shown)} of {total})"
    return txt


def to_prompt(insp):
    """Compact text form of an inspection.

    Deliberately not raw JSON: the same content as prose costs roughly a third
    of the tokens, and token efficiency is an explicit judging criterion.

    Every family in the inspection is printed. Only the per-family member lists
    and the planar-face table are capped, and each cap prints its true total —
    the executor is told to select by measured numbers from this index, so a
    number it cannot see here is a number it will make up.
    """
    s = insp["summary"]
    L = [
        f"SOLID {s['file']}  valid={s['valid']}  solids={s['solids']}",
        f"  {s['faces']} faces, {s['edges']} edges, {s['vertices']} vertices",
        f"  surfaces: " + ", ".join(f"{k}x{v}" for k, v in s["surface_types"].items()),
        f"  volume {s['volume_mm3']} mm^3, area {s['area_mm2']} mm^2",
        f"  bbox size {s['bbox_size']}  min {s['bbox_min']}  max {s['bbox_max']}",
        "  Any list cut for length says '(showing N of M)' — a list without "
        "that marker is complete.",
    ]

    # WHICH BODY OWNS WHAT. Printed first, because on a multi-body part it is
    # the fact every other row depends on: face_idx and edge_idx are positions
    # in the WHOLE file's face/edge list, and a target described with entities
    # from two different bodies cannot be built.
    # Big assemblies would otherwise spend the whole prompt on tables. The
    # caps shrink with the part so the index stays roughly constant in size;
    # every cut still prints its true total, so a shortened list never reads
    # as an absent feature.
    n_faces = s.get("faces") or 0
    big = n_faces > 200
    # The SOLIDS table is NOT capped like the feature tables below. Those print
    # their true totals, so a shortened list still tells the planner the
    # feature exists; this one has no such fallback, and the block right below
    # tells the agents "A target must live in ONE body" — so a body that is not
    # listed is a target that cannot be named at all.
    #
    # Measured on task 3YH2WFSRM22W7DKT_1769782403 ("change the plug for a
    # Europlug"): the part has 20 bodies, the cap was 10, and s19 — the actual
    # plug, the entire subject of the instruction — was among the omitted ten.
    # The strategist anchored on two BSPLINE sweep patches in the middle of the
    # cable instead, and six attempts, one replan and $1.30 went into building
    # a new plug onto the cord while the real one was left untouched; zero
    # voxel overlap with ground truth. 12 of the 48 benchmark parts have more
    # than 10 bodies, so the cap was hiding targets on a quarter of the set.
    # Cost of listing them all: ~28 tokens per body on a ~5.5k-token index.
    cap_solids = len(insp.get("solids") or ())
    cap_generic = 20 if big else _PRINT_GENERIC_FACES
    cap_inner = 10 if big else _PRINT_INNER
    # Splitting a family per owning body is what makes it citable, but on an
    # assembly one radius can appear on a dozen bodies. Cap the bodies shown
    # per family (the rest are named by count, not hidden) and the number of
    # families, so the split buys correctness without buying the prompt.
    cap_families = 16 if big else _PRINT_FAMILIES
    cap_owner_lines = 4 if big else 12

    solids = insp.get("solids") or []
    if len(solids) > 1:
        L.append("")
        L.append(f"SOLIDS — this file contains {len(solids)} SEPARATE BODIES. "
                 f"Every face_idx/edge_idx below carries the body that owns it "
                 f"as `s<N>`. A target must live in ONE body: never combine "
                 f"tags whose s<N> differ.")
        for r in solids[:cap_solids]:
            L.append(f"  s{r['solid']:<3} vol={str(r['volume']):<12} "
                     f"faces={r['faces']:<5} edges={r['edges']:<5} "
                     f"bbox {r['bbox_min']}..{r['bbox_max']}")
        if len(solids) > cap_solids:
            L.append(f"  ... showing {cap_solids} of {len(solids)} bodies "
                     f"(largest-index bodies omitted)")

    L += [
        "",
        "CIRCULAR EDGE FAMILIES (candidate holes / round features; "
        "edge_idx = unique edge tags; centers are TRUE circle centers, which "
        "for a partial arc is NOT where Edge.Center() puts its centroid):",
    ]
    circ = insp["circular_edges"]["groups"]
    multi = len(solids) > 1
    eown = insp.get("edge_owner")
    ctr_cap = 2 if big else _PRINT_CENTERS
    for g in circ[:cap_families]:
        arcs = g.get("partial_arcs") or 0
        arc_note = f" partial_arcs={arcs}/{g['count']}" if arcs else ""
        head = (f"  r={g['radius']:<8} d={g['diameter']:<8}"
                f" [{g.get('scale','feature')}]{arc_note}")
        # One line PER OWNING BODY. A single line pooling "22 edges of r=12.7"
        # across five bodies is not a citable target: the planner cannot tell
        # which ids sit on the body it named, and that is exactly how a
        # sub-goal ends up naming one body and citing another's entities.
        by_owner = defaultdict(list)
        for pos, j in enumerate(g["edge_idx"]):
            by_owner[_owner_of(eown, j)].append(pos)
        owners_sorted = sorted(by_owner, key=lambda x: (x is None, x))
        for o in owners_sorted[:cap_owner_lines]:
            pos = by_owner[o]
            ids = [g["edge_idx"][p] for p in pos]
            ctrs = [g["centers"][p] for p in pos if p < len(g["centers"])]
            tag = f" s{o}" if multi and o is not None else ""
            cap = max(ctr_cap, len(ctrs)) if len(ctrs) <= _FAMILY_PRINT_WHOLE_UPTO else ctr_cap
            L.append(f"{head}{tag} count={len(ids):<4}"
                     f" centers={_capped(ctrs, cap)}"
                     f" edge_idx={_capped(ids, _PRINT_IDX)}")
        if len(owners_sorted) > cap_owner_lines:
            rest = owners_sorted[cap_owner_lines:]
            L.append(f"{head} ... also on {len(rest)} more bodies: "
                     + ",".join(f"s{o}" for o in rest[:12]))
    if len(circ) > cap_families:
        L.append(f"  ... showing {cap_families} of {len(circ)} circular-edge "
                 f"families (feature-scale first)")
    L.append(f"  ({insp['circular_edges']['note']})")

    # THE FEATURES THOSE RINGS BOUND. Two rings of one radius sharing an axis
    # are the two mouths of ONE bore/port/boss, not two holes. Said plainly
    # here so a planner reads a tube as a tube — with a length and two ends —
    # instead of as a pair of unrelated coordinates it might "mirror" onto a
    # spot that is already occupied.
    axf = insp.get("axial_features") or []
    if axf:
        L.append("")
        L.append("BORES / PORTS these rings form (paired mouths of one "
                 "feature — the pair IS one hole, do not count it twice, and "
                 "a NEW one must go where none of these already is):")
        for f in sorted(axf, key=lambda r: -float(r["radius"] or 0))[:_PRINT_AXIAL]:
            at = ", ".join("*" if v is None else str(v) for v in f["at"])
            L.append(f"  r={f['radius']:<8} axis={f['axis']} "
                     f"at=({at})  spans {f['axis']} {f['span'][0]}..{f['span'][1]} "
                     f"(len {f['length']})  mouths={f['n']} "
                     f"edge_idx={_capped(f['edge_idx'], 8)}")
        if len(axf) > _PRINT_AXIAL:
            L.append(f"  ... showing {_PRINT_AXIAL} of {len(axf)} such features "
                     f"(largest radius first)")

    cyl = insp["cylindrical_faces"]
    if cyl:
        L.append("")
        # face_idx -> the faces it shares an edge with, and the set of planar
        # face indices, so a blend family can report what it sits against.
        _adj_of = defaultdict(set)
        for _a, _b in (insp.get("face_adjacency") or ()):
            _adj_of[_a].add(_b)
            _adj_of[_b].add(_a)
        _planar_idx = {f["face_idx"] for f in (insp.get("planar_faces") or ())}
        L.append("CYLINDRICAL FACE FAMILIES (hole walls / bosses; "
                 "face_idx = unique face tags; axis = the measured direction "
                 "each bore/boss actually runs — anchor directions to it, "
                 "never to a guessed world axis; sweep = how far each face "
                 "wraps around that axis: ~360 is a full bore, ~180 a slot "
                 "end-wall, and ~90 is a ROUNDED CORNER BLEND, not a hole or "
                 "slot at all — never build a feature out of 90-degree "
                 "faces):")
        cyl_lines = []
        for g in cyl[:cap_families]:
            # Same per-body split as the circular families above, for the same
            # reason: a family pooled across bodies cannot be cited.
            groups_by_owner = defaultdict(list)
            for f in g["faces"]:
                groups_by_owner[f.get("solid")].append(f)
            owners_sorted = sorted(groups_by_owner,
                                   key=lambda x: (x is None, x))
            for owner in owners_sorted[:cap_owner_lines]:
                cyl_lines.append((g, owner, groups_by_owner[owner]))
        for g, owner, members in cyl_lines:
            ctrs = [f["center"] for f in members]
            idxs = [f["face_idx"] for f in members]
            axes = [tuple(f["axis"]) for f in members if f.get("axis")]
            if not axes:
                ax_txt = ""
            elif len(set(axes)) == 1:
                ax_txt = f" axis={list(axes[0])}"
            else:
                ax_txt = (" axes=" +
                          str([list(a) for a in dict.fromkeys(axes)][:4]))
            sweeps = [f["sweep_deg"] for f in members
                      if f.get("sweep_deg") is not None]
            if not sweeps:
                sw_txt = ""
            elif len(set(sweeps)) == 1:
                sw_txt = f" sweep={sweeps[0]}deg"
                if sweeps[0] <= _BLEND_SWEEP_DEG:
                    sw_txt += " (CORNER BLEND, not a hole/slot wall)"
            else:
                sw_txt = " sweeps=" + str(sorted(set(sweeps))[:4])
            own = f" s{owner}" if multi and owner is not None else ""
            cap = max(ctr_cap, len(ctrs)) if len(ctrs) <= _FAMILY_PRINT_WHOLE_UPTO else ctr_cap
            # WHICH PLANAR FACES THIS FAMILY TOUCHES. Instructions discriminate
            # by adjacency constantly — "if the edge with the BIGGER RADIUS is
            # considered the top", "the flat end, the one WITHOUT fillet", "the
            # left side is MISSING radii" — and the family line alone cannot
            # answer any of them: it carries a radius and a centre, never what
            # the blend sits against, so the planner picks a side by guess.
            #
            # Measured on task 27 (B7A2N74ZJBF9MZHU_1770172545): the
            # instruction DEFINED the top as the bigger-radius side. The r=10
            # family adjoins planar face #34, making #34 the top; the plan
            # declared #34 the BOTTOM and built the entire support ring on the
            # wrong side. Both sub-goals were QA-accepted on their first
            # attempt — the pipeline built exactly what it planned — and
            # diff_f1 came out 0.0 against a human who scored 0.9986.
            #
            # The pairs are already computed for the render colouring
            # (`face_adjacency`); this only surfaces them to the planner.
            touch = sorted({b for a in idxs for b in _adj_of.get(a, ())
                            if b in _planar_idx})
            adj_txt = (f" adjoins_planar={_capped(touch, _PRINT_IDX)}"
                       if touch else "")
            L.append(f"  r={g['radius']:<8} d={g['diameter']:<8}{own} "
                     f"count={len(members):<4} [{g.get('scale','feature')}]"
                     f"{ax_txt}{sw_txt}{adj_txt}"
                     f" centers={_capped(ctrs, cap)}"
                     f" face_idx={_capped(idxs, _PRINT_IDX)}")
        if len(cyl) > _PRINT_FAMILIES:
            L.append(f"  ... showing {_PRINT_FAMILIES} of {len(cyl)} cylindrical-"
                     f"face families (feature-scale first)")

    plan = insp["planar_faces"]
    L.append("")
    L.append("PLANAR FACES, largest first (idx, area, center, normal, vertical):")
    for f in plan[:_PRINT_PLANAR]:
        own = f" s{f['solid']}" if multi and f.get("solid") is not None else ""
        L.append(f"  #{f['face_idx']:<4}{own} area={f['area']:<9} c={f['center']}"
                 f" n={f.get('normal')} vertical={f.get('vertical')}")
    if len(plan) > _PRINT_PLANAR:
        L.append(f"  ... showing {_PRINT_PLANAR} of {len(plan)} planar faces "
                 f"(largest first)")

    # EVERY OTHER SURFACE TYPE. The three sections above are the specialised
    # views; this is the backstop that makes the index honest. Splines, tori,
    # cones and spheres are 38% of the faces in this benchmark and used to be
    # invisible — so a target sitting on one could not be named, and the
    # planner substituted whatever it could see, often on the wrong body.
    gen = [f for f in (insp.get("all_faces") or [])
           if f["type"] not in ("PLANE", "CYLINDER")]
    if gen:
        gen = sorted(gen, key=lambda r: -float(r["area"] or 0))
        L.append("")
        L.append("OTHER FACES — every remaining surface type (spline, torus, "
                 "cone, sphere ...), largest first. n_wires>1 means the face "
                 "has a hole or slot opening cut through it:")
        for f in gen[:cap_generic]:
            own = f" s{f['solid']}" if multi and f.get("solid") is not None else ""
            L.append(f"  #{f['face_idx']:<4}{own} {f['type']:<8} "
                     f"area={f['area']:<9} c={f['center']} "
                     f"n_wires={f['n_wires']}")
        if len(gen) > cap_generic:
            L.append(f"  ... showing {cap_generic} of {len(gen)} "
                     f"other faces (largest first)")

    # OPENINGS. "The slot rim", "the hole edges on the top face" resolve here
    # and nowhere else: an inner wire IS the opening's edge loop.
    inner = insp.get("inner_wires") or []
    if inner:
        L.append("")
        L.append("FACES WITH INNER BOUNDARY LOOPS (each inner loop is an "
                 "OPENING cut through that face — a hole or slot mouth; "
                 "inner_edges are the edge tags forming those loops, which is "
                 "what a rim fillet or chamfer must target. [BLIND] = the opening bottoms out in material (it has a floor and can be opened up); [THROUGH] = it already passes clean out the far side, so 'make it go through' is already true of it):")
        for f in sorted(inner, key=lambda r: -float(r["area"] or 0))[:cap_inner]:
            own = f" s{f['solid']}" if multi and f.get("solid") is not None else ""
            st = (insp.get("pocket_state") or {}).get(f['face_idx'])
            st_txt = f" [{st.upper()}]" if st else ""
            L.append(f"  #{f['face_idx']:<4}{own} {f['type']:<8} "
                     f"area={f['area']:<9} c={f['center']} "
                     f"loops={f['n_inner']}{st_txt} "
                     f"inner_edges={_capped(f['inner_edges'], _PRINT_IDX)}")
        if len(inner) > cap_inner:
            L.append(f"  ... showing {cap_inner} of {len(inner)} faces with "
                     f"inner loops (largest first)")

    # Multi-body files: which solids exist and — MEASURED, not guessed — which
    # pairs actually interpenetrate. For any collision/interference/clearance
    # instruction this table IS the answer to "which bodies": nine measured
    # attempts on the coffeepot guessed the bodies from face radii, cut solids
    # that were 28 mm apart, and shipped nothing.
    inter = insp.get("interference")
    if inter:
        # The per-body table is already printed at the top (same enumeration,
        # `shape.Solids()[N]`), so only the measured overlaps are added here.
        pairs = inter["pairs"]
        if pairs or not solids:
            L.append("")
        if pairs:
            L.append("MEASURED OVERLAPPING PAIRS (boolean-common volume and its "
                     "centroid — assemblies interpenetrate at joints BY DESIGN, "
                     "so an instruction about a collision means the pair whose "
                     "location matches the parts it names, not necessarily the "
                     "biggest):")
            for p in pairs[:_PRINT_PAIRS]:
                L.append(f"    solids #{p['a']} ∩ #{p['b']}  vol={p['volume']} mm^3"
                         f"  at {p['center']}")
            if len(pairs) > _PRINT_PAIRS:
                L.append(f"    ... showing {_PRINT_PAIRS} of {len(pairs)} "
                         f"overlapping pairs (largest first)")
            L.append("    To remove a collision keeping body A intact: "
                     "cut B with A — `sols = base.Solids(); "
                     "out = cq.Compound.makeCompound([s for k, s in "
                     "enumerate(sols) if k != B] + [sols[B].cut(sols[A])])` — "
                     "and print the pair's common volume before and after.")
        else:
            L.append("  no overlapping solid pairs measured"
                     + (" (pair scan truncated)" if inter.get("truncated") else ""))
    return "\n".join(L)


# FEATURE COLOURING. The index above gives the agents face_idx tags, and the
# render gives them a picture — but nothing connects the two. On the 90-face
# rotor, "face_idx=[9, 11, 13]" and a uniformly grey isometric view leave the
# model to guess WHICH of the grey cylinders those three are, and a wrong guess
# is indistinguishable from a right one until the score comes back. Painting one
# colour per feature family and printing the colour -> face_idx legend next to
# the picture turns that guess into a lookup.
#
# The palette is fixed, ordered by how far apart the colours read at 512 px on a
# white background, and NAMED: the legend is text, so the name is the join key
# between what the model sees and what it can select. Twelve is both the point
# where further colours stop being reliably distinguishable and the cap on how
# many families are worth colouring at once.
# Sequenced so consecutive assignments are never near-neighbours in hue: the
# first sample renders put magenta, pink and purple on adjacent faces of one
# wrench body, and "the MAGENTA face" stops being a usable verbal handle when
# pink sits touching it. The three red-adjacent hues (purple, magenta, pink)
# are spread to positions 5, 9 and 11 so at most one appears in a typical
# plan (<= ~10 groups), and never two in a row.
_COLOR_PALETTE = [
    ("red",     [0.85, 0.10, 0.10]),
    ("blue",    [0.10, 0.30, 0.85]),
    ("green",   [0.10, 0.65, 0.20]),
    ("orange",  [1.00, 0.55, 0.00]),
    ("purple",  [0.55, 0.15, 0.75]),
    ("cyan",    [0.10, 0.80, 0.85]),
    ("yellow",  [0.95, 0.85, 0.10]),
    ("teal",    [0.00, 0.50, 0.50]),
    ("magenta", [0.95, 0.15, 0.70]),
    ("brown",   [0.50, 0.30, 0.10]),
    ("pink",    [1.00, 0.65, 0.75]),
    ("olive",   [0.50, 0.50, 0.10]),
]

# Everything not in a coloured group. Light enough that the coloured families
# pop against it, dark enough to still show shading and silhouette. Still used
# by the change-colouring (where gray MEANS "inherited, untouched") and as the
# fallback when an inspection carries no face adjacency.
GRAY_RGB = [0.75, 0.75, 0.75]
GRAY_NAME = "gray"

# Muted shades for every face that is NOT in a feature group. Deliberately
# non-vivid so the feature families above still pop, but distinct enough from
# each other that two touching non-feature faces never read as one surface —
# which is what a uniform gray remainder did: on the lug bracket the tab wall
# and the web behind it merged into one gray blob and the model mis-attributed
# the tab-hole rims. Assignment is adjacency-aware (touching faces get
# different shades), so the count only needs to beat the leftover subgraph's
# clique size, and five does in practice.
# Values are deliberately darker than they should read: the OCC viewer's
# lighting lifts everything toward white, and the first calibration pass
# ([0.55..0.82] tints) rendered as uniform near-white — indistinguishable from
# the gray they replace.
# No neutral shade in the set: a near-gray "silver" rendered exactly like the
# gray remainder it was supposed to replace, and the part still read as
# uncoded. Four real tints, all clearly muted next to the vivid family palette.
_MUTED_PALETTE = [
    ("slate", [0.30, 0.42, 0.62]),
    ("tan",   [0.66, 0.48, 0.24]),
    ("sage",  [0.34, 0.55, 0.32]),
    ("mauve", [0.55, 0.35, 0.53]),
]

# Palette slots reserved for feature groups (families + planar picks). Kept
# below the full 12 so the vivid colours stay unambiguous — one vivid colour is
# one selectable feature family, and the muted shades above are visibly "not a
# family".
_FEATURE_GROUP_CAP = 8

# face_idx printed per legend row. Lower than `_PRINT_IDX` because a legend row
# has to stay one line next to a picture; the "(showing 12 of 32)" marker keeps
# the truncation honest, exactly as everywhere else in this file.
_PRINT_COLOR_IDX = 12

# At most this many individually-coloured planar faces. Filling every leftover
# colour slot with a planar face turned a 26-face wrench into a rainbow with
# only 6 gray faces left — the "highlighted features against a neutral part"
# reading drowned, and the part's own body carried a colour as if it were a
# feature. Cylindrical families are the valuable groups; a few large planar
# faces are enough for "the top face" pointing, and everything else stays gray.
_MAX_PLANAR_COLORS = 4


def color_plan(insp, max_groups=_FEATURE_GROUP_CAP):
    """Which faces to paint which colour, and the legend that explains it.

    Returns a list of group dicts, in the order the colours should be listed:

        {"name": "red", "rgb": [0.85, 0.1, 0.1],
         "label": "cylindrical r=7.62 d=15.24 x16",
         "face_ids": [9, 11, 13, ...],          # every face in the group
         "tags":     "face_idx=[9, 11, 13]"}    # the same list, printable

    Assignment order is "most likely to be the thing the instruction names,
    first": every cylindrical-face family (hole walls and bosses, biggest family
    first), then the largest individual planar faces one colour each, until the
    palette runs out. Circular-EDGE families get no colour of their own — an
    edge has no surface to paint — but every hole edge bounds a cylindrical face
    that does, so its family is visible through the wall it belongs to.

    The last entry is always the grey sentinel: `name` "gray", EMPTY `face_ids`,
    and a label saying it stands for every face not listed above. It exists so a
    caller printing the legend can print that row too without special-casing it;
    consumers that colour geometry must skip entries with no `face_ids`.

    Returns [] on any failure — a render without colours beats no render.
    """
    try:
        groups = []
        # The index's own order: feature-scale families before blend-scale,
        # bigger first within a band. Re-sorting by raw family size handed the
        # vivid colours to 19-face micro-blend families (r=0.1 and below on
        # the lug bracket) while the r=1.4 tab-hole walls — the family the
        # instruction was actually about — fell off the palette entirely once
        # the cap dropped to _FEATURE_GROUP_CAP.
        fams = list(insp.get("cylindrical_faces") or [])
        for fam in fams:
            if len(groups) >= max_groups:
                break
            ids = [f["face_idx"] for f in fam.get("faces") or []]
            if not ids:
                continue
            name, rgb = _COLOR_PALETTE[len(groups) % len(_COLOR_PALETTE)]
            groups.append({
                "name": name,
                "rgb": list(rgb),
                "label": (f"cylindrical r={fam['radius']} d={fam['diameter']} "
                          f"x{fam.get('count', len(ids))}"),
                "face_ids": ids,
                "tags": "face_idx=" + _capped(ids, _PRINT_COLOR_IDX),
            })

        # Planar faces are already sorted largest-area-first by `planar_faces`,
        # so filling slots in order colours the surfaces an instruction is most
        # likely to mean by "the top face" / "that side" — but only a few
        # (_MAX_PLANAR_COLORS): most of the part must stay gray.
        used = {i for g in groups for i in g["face_ids"]}
        planar_coloured = 0
        for f in insp.get("planar_faces") or []:
            if len(groups) >= max_groups or planar_coloured >= _MAX_PLANAR_COLORS:
                break
            idx = f.get("face_idx")
            if idx is None or idx in used:
                continue
            used.add(idx)
            name, rgb = _COLOR_PALETTE[len(groups) % len(_COLOR_PALETTE)]
            groups.append({
                "name": name,
                "rgb": list(rgb),
                "label": (f"planar face #{idx} area={f.get('area')} "
                          f"n={f.get('normal')}"),
                "face_ids": [idx],
                "tags": f"face_idx=[{idx}]",
            })
            planar_coloured += 1

        if not groups:
            return []

        # FULL COVERAGE: every face not in a feature group gets a muted shade,
        # chosen so no two faces sharing an edge share a shade. Nothing is left
        # gray — an all-gray remainder let adjacent non-feature surfaces merge
        # into one blob in the renders. Without adjacency data (an inspection
        # from before it was recorded) fall back to the gray sentinel exactly
        # as before.
        total = (insp.get("summary") or {}).get("faces")
        adj = insp.get("face_adjacency")
        if not total or adj is None:
            rest = ("" if total is None
                    else f" ({total - len(used)} of {total} faces)")
            groups.append({
                "name": GRAY_NAME,
                "rgb": list(GRAY_RGB),
                "label": "all other faces" + rest,
                "face_ids": [],
                "tags": "face_idx=(everything not listed above)",
            })
            return groups

        neighbours = defaultdict(set)
        for a, b in adj:
            neighbours[a].add(b)
            neighbours[b].add(a)
        # Hardest faces first: a face with many neighbours picks its shade
        # while every shade is still available to it.
        left = sorted((i for i in range(total) if i not in used),
                      key=lambda i: -len(neighbours[i]))
        shade_of, buckets = {}, {name: [] for name, _ in _MUTED_PALETTE}
        for i in left:
            taken = {shade_of[j] for j in neighbours[i] if j in shade_of}
            # Least-used free shade, not first free shade: first-free painted
            # every large face silver (big faces rarely touch each other), so
            # the body still rendered as one uniform gray mass with coloured
            # trim. Balancing spreads the shades across the visible surfaces.
            free = [nm for nm, _ in _MUTED_PALETTE if nm not in taken]
            if free:
                pick = min(free, key=lambda nm: len(buckets[nm]))
            else:               # leftover clique bigger than the shades:
                pick = min(     # reuse the least-conflicting one
                    (nm for nm, _ in _MUTED_PALETTE),
                    key=lambda nm: sum(1 for j in neighbours[i]
                                       if shade_of.get(j) == nm))
            shade_of[i] = pick
            buckets[pick].append(i)
        rgb_of = dict(_MUTED_PALETTE)
        for name, _ in _MUTED_PALETTE:
            ids = sorted(buckets[name])
            if not ids:
                continue
            groups.append({
                "name": name,
                "rgb": list(rgb_of[name]),
                "label": ("other faces — muted shade, NOT a feature family; "
                          "shades only tell touching faces apart"),
                "face_ids": ids,
                "tags": "face_idx=" + _capped(ids, _PRINT_COLOR_IDX),
            })
        return groups
    except Exception:
        return []


# Writing a solid to STEP and reading it back is NOT lossless. Measured on the
# sample bracket, a pure load-and-re-export shifts the volume by 0.035% and
# perturbs 63 of 266 face areas at the 4th decimal — OCC re-approximates spline
# surfaces on write. So an exact geometric hash can never identify an unchanged
# solid, and any "did anything change?" test must be tolerance-based. For
# reference, the real 0.2 mm chamfer on this part is a 0.6% volume change, so
# round-trip noise is roughly 6% of the signal for a small edit.
_ROUNDTRIP_VOLUME_TOL_PCT = 0.05
# Absolute ceiling on "unchanged": above this many mm^3 of volume delta an
# attempt is never auto-rejected as a no-op, whatever the percentage says.
_NOOP_MAX_ABS_MM3 = 5.0
# ...and a RELATIVE floor, because 5 mm^3 does not mean the same thing on a
# 300 mm^3 bracket as on a 31,000,000 mm^3 espresso machine. Measured on task
# B7A2N74ZJBF9MZHU_1770174308: a genuine no-op (the selector matched nothing
# and the script returned the input) re-exported with a 5.524 mm^3 delta —
# 0.000018% of the part — so the gate missed it by half a cubic millimetre,
# a full QA call was spent discovering nothing had happened, and the executor
# never got the "your selector matched nothing" diagnosis it needed.
_NOOP_REL_FRAC = 1e-6


def is_noop(before_step, after_step, diff=None):
    """True when the edit almost certainly changed nothing.

    Deliberately conservative: a false positive would discard genuine work,
    while a false negative merely costs one QA call. Requires identical face
    and edge counts, an unchanged bounding box, and a volume change below the
    kernel's own round-trip noise floor.

    The bbox test is on POSITION, not just size: `bbox_changed` compares extents,
    so a part that was translated bodily has the same size, the same topology and
    the same volume, and would otherwise be reported as an unchanged part. It is
    not — it is a frame drift, which scores zero for a different reason and needs
    different feedback, so it must fall through to `frame_drift()`.
    """
    d = diff or compare(before_step, after_step)
    faces, edges = d.get("faces") or [0, 0, 1], d.get("edges") or [0, 0, 1]
    pct = d.get("volume_change_pct")
    moved = bool(d.get("envelope_growth"))
    # The relative tolerance SCALES WITH THE PART: 0.05% of the 8.4e6 mm^3
    # coffeepot is 4200 mm^3 — larger than that task's entire real edit
    # (a 2695 mm^3 collision shave). A delta that is big in absolute terms is
    # real work even when the percentage reads as noise, so it is NOT clearly
    # a no-op: it falls through to QA, which costs one call, where a wrong
    # auto-reject costs the attempt. (Observed true no-ops reproduce the
    # volume to 4 decimals, so the absolute gate does not reclassify them.)
    try:
        abs_dv = abs(float((d.get("volume_mm3") or [0, 0, 0])[2] or 0.0))
    except (TypeError, ValueError):
        abs_dv = 0.0
    return (faces[2] == 0 and edges[2] == 0
            and not d.get("bbox_changed", True) and not moved
            and pct is not None and abs(pct) < _ROUNDTRIP_VOLUME_TOL_PCT
            and abs_dv < _NOOP_MAX_ABS_MM3)


_AXES = ("X", "Y", "Z")
# Below this the difference is kernel round-trip noise, not a real move.
_FRAME_TOL_MM = 1e-3


def envelope_growth(a, b):
    """Per-face growth of the bounding box, in mm, keyed '-X'/'+X'/'-Y'/...

    The scorer voxelizes in ABSOLUTE world coordinates with no registration, so
    *which side* of the part grew is a scored fact, not a detail. A 0.5 mm
    flange built downwards off the bottom face instead of upwards into the
    existing slab is the same feature in the wrong half-space, and it costs
    more than most outright modelling errors. `bbox_changed` alone cannot see
    this — both edits change the bbox — so the direction is reported per face.
    """
    out = {}
    for i, ax in enumerate(_AXES):
        lo = a["bbox_min"][i] - b["bbox_min"][i]   # >0 -> grew in -ax
        hi = b["bbox_max"][i] - a["bbox_max"][i]   # >0 -> grew in +ax
        if abs(lo) > _FRAME_TOL_MM:
            out[f"-{ax}"] = _fmt(lo, 4)
        if abs(hi) > _FRAME_TOL_MM:
            out[f"+{ax}"] = _fmt(hi, 4)
    return out


# A declaration is a plan, not a measurement, so the gate only fires on moves
# that are large relative to the part. Calibrated on two real cases:
#   rotor  (diag 507 mm): the HUMAN's own third blade grew +-X by 5.45 mm
#                         (1.1% of the diagonal) — must NOT be rejected;
#                         the model's wrong-axis rotation grew +-Y by 123 mm
#                         (24%) — must be.
#   flange (diag 11.8 mm): the wrong-side 0.5 mm drop in -Z is 4.2% — must be.
# 3% sits with an order of magnitude of clearance on both sides.
_ENVELOPE_SLACK_FRAC = 0.03


def _diagonal(size):
    try:
        return (size[0] ** 2 + size[1] ** 2 + size[2] ** 2) ** 0.5
    except Exception:
        return 0.0


def undeclared_envelope_moves(diff, declared):
    """{face: mm} for bbox faces that moved without the sub-goal saying so.

    The strategist declares which faces an edit may move; anything else moving
    means the material went on the wrong side of its reference face. Measured
    over the 48 benchmark requests, 26 human edits leave the envelope untouched
    and 22 move it — so this cannot be a blanket rule, only a check against a
    stated intent. `declared=None` means nothing was stated: no check.

    A face that *shrank* counts as moved: material removed from the outside is
    just as much a change of the part's silhouette as material added.
    """
    if declared is None:
        return {}
    ok = {str(d).strip().upper().replace(" ", "") for d in declared}
    slack = max(_FRAME_TOL_MM,
                _ENVELOPE_SLACK_FRAC * _diagonal(diff.get("bbox_before") or []))
    return {face: mm for face, mm in (diff.get("envelope_growth") or {}).items()
            if face not in ok and abs(mm) > slack}


def frame_drift(a, b):
    """Detect that the whole part MOVED rather than being edited.

    Returns a short description, or None when the frame is intact. A rigid
    translation or a unit rescale leaves the part looking perfect in every
    render while scoring near zero, because none of the three metrics aligns
    the prediction to the ground truth first (`pre_align=False` throughout
    evals_diff.py / evals_feature_geometric.py). It is worth catching before
    spending a QA call on pictures that cannot show it.
    """
    same_topo = a["faces"] == b["faces"] and a["edges"] == b["edges"]
    sa, sb = a["bbox_size"], b["bbox_size"]

    if same_topo and all(abs(sa[i] - sb[i]) <= _FRAME_TOL_MM for i in range(3)):
        d = [_fmt(b["bbox_min"][i] - a["bbox_min"][i], 4) for i in range(3)]
        if any(abs(v) > _FRAME_TOL_MM for v in d):
            return (f"the whole part was TRANSLATED by {d} mm — same size, same "
                    f"topology, different position")

    ratios = [sb[i] / sa[i] for i in range(3) if sa[i] > _FRAME_TOL_MM]
    if same_topo and ratios and max(ratios) - min(ratios) < 1e-4 \
            and abs(ratios[0] - 1.0) > 1e-3:
        return (f"the whole part was RESCALED by x{_fmt(ratios[0], 4)} — the "
                f"units are wrong, not the geometry")
    return None


# A duplicated body left at its source's position is the one failure mode that
# beats every cheap gate: the output is a COMPOUND, so `Shape.Volume` sums the
# solids and the summed volume rises by a full blade's worth, while the space
# the part occupies does not move by a single voxel. Measured on the rotor
# "add a third blade" request, an exact coincident copy of blade A added
# +95,495 mm^3 of summed volume, passed the no-op gate, rendered pixel-identical
# in all six views, drew a 0.62 partial-accept out of QA — and scored diff
# F1 = 0.0, because the scorer voxelizes OCCUPIED space.
#
# The one number that separates the two cases is the volume of the FUSION of
# all solids: real new material raises it, a coincident copy does not.
_PHANTOM_VOLUME_RISE_PCT = 1.0      # below this the summed rise is not a new body
_PHANTOM_OCCUPIED_FRAC = 0.1        # occupied gain under 10% of it -> phantom


def occupied_volume(step_path):
    """Volume of the SPACE a shape occupies — its solids fused, not summed.

    `Shape.Volume` on a compound adds its solids up and double-counts anything
    overlapping; this fuses first so coincident bodies count once. Returns None
    when the fuse fails (OCC gives up on some imported compounds), which
    silently disarms the caller's check rather than inventing a number.
    """
    try:
        solids = load_shape(step_path).Solids()
        if not solids:
            return None
        fused = solids[0]
        for s in solids[1:]:
            fused = fused.fuse(s)
        return _fmt(fused.Volume(), 3)
    except Exception:
        return None


# WHERE did the new material land? Nothing else in this file answers that, and
# no render can: six greyscale views of a 200 mm bracket cannot show that the
# embossed text sits 48 mm from where the sub-goal put it. Measured on the text
# request, the sub-goal named a target centre of (x=-0.273, z=-51.776), the
# executor built the text at z=-6.8, QA accepted the pictures, and the run
# scored diff F1 = 0.0. A measured centroid of the faces that appeared turns
# that into a number QA can subtract from the coordinates it asked for.
#
# The face key is deliberately COARSE. Per the round-trip note above, an export
# / import cycle perturbs 63 of 266 face areas at the 4th decimal, so keying on
# full precision would report an untouched, re-exported part as ~25% new faces.
# Two decimal places (0.01 mm of position, 0.01 mm^2 of area) sit far above that
# noise and far below any real feature.
_FACE_KEY_DP = 2

# WHEN the question is meaningless. A rebuild, a mirror-and-union or a global
# rescale re-keys most of the part, and "the centroid of the new faces" is then
# the centre of the whole part — a confident-looking number pointing at nothing.
# The benchmark's mirror task ("mirror the full solid about x=80 and union")
# currently scores 0.998, so a spurious line in front of QA there is a live
# regression risk and the bail-out MUST fire on it.
#
# The obvious rule — "bail when new faces exceed half the after-faces" — does
# NOT work, measured on the two tasks it has to separate:
#     text emboss   26 -> 91 faces, 73 new (80% of after), 18 of 26 before kept
#     mirror+union  12 -> 18 faces, 11 new (61% of after),  7 of 12 before kept
# It silences the text case, which is the whole reason this exists: an emboss
# re-splits every host face it touches AND adds one face per glyph stroke, so a
# *localized* feature legitimately re-keys most of a coarse part. The two are
# separated instead by what a body-scale operation does that a feature cannot:
#   * the ENVELOPE moves a lot — the mirror doubles the part, bbox diagonal
#     108 -> 212 mm (+95%); the emboss grows it 0.5 mm on one face (+0.02%);
#   * the ORIGINAL faces stop existing — a from-scratch rebuild or a rescale
#     keeps ~0% of them (the emboss keeps 69%, the mirror 58%);
#   * the new faces are SCATTERED over the part instead of clustered (below).
_NEW_REGION_ENVELOPE_FRAC = 0.20   # bbox diagonal change -> body-scale op
_NEW_REGION_KEEP_FRAC = 0.50       # before-faces that must survive the edit
_NEW_REGION_CLUSTER_FRAC = 0.10    # cluster radius, as a fraction of the diagonal
_NEW_REGION_CLUSTER_MIN = 0.50     # of the new faces must fall in that cluster


def _median(vals):
    v = sorted(vals)
    n = len(v)
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def _face_props(faces):
    """[(area, (x, y, z)), ...] — one mass-property evaluation per face.

    `Face.Area()` and `Face.Center()` each run BRepGProp over the whole face, so
    the obvious `(f.Center(), f.Area())` pays for the same integration twice.
    On the 799-face benchmark input that duplication alone cost 6 of the 12.6 s
    this function used to add to compare(). Ask the kernel once, read both off
    the same result, and hand the numbers to the caller so nothing recomputes
    them for the centroid either.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    out = []
    for f in faces:
        p = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f.wrapped, p)
        c = p.CentreOfMass()
        out.append((float(p.Mass()), (c.X(), c.Y(), c.Z())))
    return out


def _face_key(area, ctr):
    """((x, y, z), area), all at 2 dp — see `_FACE_KEY_DP`."""
    return ((round(ctr[0], _FACE_KEY_DP),
             round(ctr[1], _FACE_KEY_DP),
             round(ctr[2], _FACE_KEY_DP)),
            round(area, _FACE_KEY_DP))


# Rounding to a key is not the same as matching within a tolerance: two values
# 0.004 apart land in different 0.01 buckets whenever they straddle a bucket
# edge (0.4068 -> 0.41, 0.4024 -> 0.40). Any boolean that isolates a solid and
# recompounds the part re-exports the UNTOUCHED bodies through the kernel,
# nudging their face centres and areas by a few thousandths — so a share of
# them, always the small ones, fell out of the exact key and were reported as
# geometry this attempt created.
#
# Measured cost on a 2-solid part: a correct 1 mm chamfer cut on the hub (3
# genuinely new faces) was reported alongside 12 untouched faces scattered over
# the other body — spheres of 0.4 mm^2 displaced by 0.006 mm, area drift up to
# 0.017 mm^2. The change-coloured renders painted those 12 red across the whole
# part, QA read the red as unrequested edits and rejected four correct attempts,
# and `new_face_region` reported the "new geometry" bbox as the entire part
# instead of the chamfer. The run shipped the unedited input.
#
# So the fast exact pass stays (it resolves almost every face), and whatever it
# leaves unmatched gets a tolerance pass: same place, same size, within limits
# far below any real feature and far above kernel noise.
_FACE_MATCH_POS_TOL = 0.02        # mm; scaled up with the part's diagonal
_FACE_MATCH_AREA_REL = 0.015      # 1.5% of the larger of the two areas
_FACE_MATCH_AREA_ABS = 0.02       # mm^2 floor, so tiny faces are not held to 1.5%


def _match_face_sets(props_prior, props_target, pos_tol=_FACE_MATCH_POS_TOL):
    """Which faces exist in BOTH sets — (target indices, prior indices).

    `props_*` are `_face_props` lists. Matching is one-to-one: a face is
    consumed once, so two coincident faces of equal area in the target match
    two in the prior and a surplus copy still reads as new.
    """
    used_prior = [False] * len(props_prior)
    matched_t = set()

    buckets = defaultdict(list)
    for j, (ar, c) in enumerate(props_prior):
        buckets[_face_key(ar, c)].append(j)
    for i, (ar, c) in enumerate(props_target):
        for j in buckets.get(_face_key(ar, c), ()):
            if not used_prior[j]:
                used_prior[j] = True
                matched_t.add(i)
                break

    # Tolerance pass over the leftovers only, on a spatial hash so this stays
    # linear: a face can only match one whose centre is within `pos_tol`.
    cell = max(pos_tol, 1e-9)
    grid = defaultdict(list)
    for j, (ar, c) in enumerate(props_prior):
        if not used_prior[j]:
            grid[(int(c[0] // cell), int(c[1] // cell),
                  int(c[2] // cell))].append(j)
    for i, (ar, c) in enumerate(props_target):
        if i in matched_t:
            continue
        home = (int(c[0] // cell), int(c[1] // cell), int(c[2] // cell))
        best, best_d = None, None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in grid.get((home[0] + dx, home[1] + dy,
                                       home[2] + dz), ()):
                        if used_prior[j]:
                            continue
                        ar2, c2 = props_prior[j]
                        d = sum((c[k] - c2[k]) ** 2 for k in range(3)) ** 0.5
                        if d > pos_tol:
                            continue
                        lim = max(_FACE_MATCH_AREA_ABS,
                                  _FACE_MATCH_AREA_REL * max(ar, ar2))
                        if abs(ar - ar2) > lim:
                            continue
                        if best_d is None or d < best_d:
                            best, best_d = j, d
        if best is not None:
            used_prior[best] = True
            matched_t.add(i)
    return matched_t, {j for j, u in enumerate(used_prior) if u}


def new_face_region(before_step, after_step, _shapes=None):
    """Where the geometry that appeared in this edit actually sits.

    Returns {"n_new", "centroid", "bbox_min", "bbox_max"} for the faces present
    in `after` but not in `before`, or None whenever the answer would not mean
    anything (nothing new; a body-scale operation; the new faces scattered all
    over the part rather than forming one feature) — see the guards above.

    Cheap by construction: O(faces), one mass-property evaluation per face, no
    boolean operations — so unlike `occupied_volume` it needs no gating beyond
    the no-op skip in `compare`.

    `_shapes` lets `compare` pass the two already-loaded shapes so this costs no
    extra STEP import; it is an internal optimisation, not part of the API.
    """
    try:
        if _shapes is not None:
            sa, sb = _shapes
        else:
            sa, sb = load_shape(before_step), load_shape(after_step)

        before_faces, after_faces = sa.Faces(), sb.Faces()
        if not before_faces or not after_faces:
            return None

        bba, bbb = sa.BoundingBox(), sb.BoundingBox()
        diag_a = _diagonal([bba.xlen, bba.ylen, bba.zlen])
        diag_b = _diagonal([bbb.xlen, bbb.ylen, bbb.zlen])
        # Guard 1: the whole envelope moved -> mirror-union, rescale, rebuild.
        if diag_a > _FRAME_TOL_MM and \
                abs(diag_b - diag_a) > _NEW_REGION_ENVELOPE_FRAC * diag_a:
            return None

        # Every face of both shapes is measured exactly once, here.
        props_a = _face_props(before_faces)
        props_b = _face_props(after_faces)
        # Matching is one-to-one and tolerant of kernel re-export noise, so a
        # recompounded but untouched body does not read as new geometry.
        pos_tol = max(_FACE_MATCH_POS_TOL, 1e-4 * max(diag_a, diag_b))
        matched_b, matched_a = _match_face_sets(props_a, props_b, pos_tol)

        # Guard 2: the part the edit started from is largely gone -> the solid
        # was rebuilt or rescaled, not edited, so every face reads as "new".
        if len(matched_a) < _NEW_REGION_KEEP_FRAC * len(before_faces):
            return None

        new = [(f, ar, c)
               for i, (f, (ar, c)) in enumerate(zip(after_faces, props_b))
               if i not in matched_b]
        if not new:
            return None

        # The new set always contains a few HOST faces: an emboss re-splits the
        # face it sits on, and that re-split face keeps most of the part's area
        # at the part's centre. On the text run those four host faces drag an
        # area-weighted centroid to z=-71.8 — 65 mm from the text. So the centre
        # is found robustly: median of the new-face centres (immune to a handful
        # of huge outliers), then the area-weighted centroid of the faces
        # CLUSTERED around it, which is the feature itself.
        med = [_median([c[i] for _, _, c in new]) for i in range(3)]
        radius = _NEW_REGION_CLUSTER_FRAC * max(diag_b, _FRAME_TOL_MM)
        cluster = [(f, ar, c) for f, ar, c in new
                   if sum((c[i] - med[i]) ** 2 for i in range(3)) ** 0.5 <= radius]
        # Guard 3: no majority cluster -> the new faces are spread over the part
        # and there is no single place to report.
        if len(cluster) < _NEW_REGION_CLUSTER_MIN * len(new):
            return None

        wsum = cx = cy = cz = 0.0
        lo = [None, None, None]
        hi = [None, None, None]
        for f, ar, c in cluster:
            w = max(ar, 1e-12)
            wsum += w
            cx += w * c[0]
            cy += w * c[1]
            cz += w * c[2]
            # bbox over each new face's own GEOMETRIC extent. This used to be
            # taken over the faces' VERTICES, which is wrong for exactly the
            # shapes CAD edits are made of: a full-revolution cylinder has only
            # its two seam vertices, so a Ø44.45 mm cap measured as
            # X=-69.85..-66.675 (3.175 mm, the two radii) and Y=0 — a POINT.
            # Measured on task SUJ2G2UMJQR7PMBX_1759203739: QA was handed that
            # and rejected a correctly built cap for "zero Y extent and only
            # 3.175 mm X extent". Verified against this install: for a Ø44.45
            # cylinder the vertex box is X 22.225..22.225, Y 0..0 while the
            # face box is X/Y -22.225..22.225. Every boss, pin, cap and bore
            # in the benchmark is a surface of revolution, so the vertex form
            # under-reports the most common feature there is.
            fbb = f.BoundingBox()
            corners = ((fbb.xmin, fbb.ymin, fbb.zmin),
                       (fbb.xmax, fbb.ymax, fbb.zmax))
            for p in corners:
                for i in range(3):
                    if lo[i] is None or p[i] < lo[i]:
                        lo[i] = p[i]
                    if hi[i] is None or p[i] > hi[i]:
                        hi[i] = p[i]
        if wsum <= 0 or lo[0] is None:
            return None
        return {
            # The faces this region actually describes: the clustered ones. Any
            # outliers are re-split host faces, which are modified, not new.
            "n_new": len(cluster),
            "centroid": [_fmt(cx / wsum), _fmt(cy / wsum), _fmt(cz / wsum)],
            "bbox_min": [_fmt(v) for v in lo],
            "bbox_max": [_fmt(v) for v in hi],
        }
    except Exception:
        return None


# Change-attribution colours for the QA renders. The current attempt is always
# red — it is the thing being judged, so it must be the colour that pops the
# hardest — and earlier accepted steps count backwards through the rest.
_CHANGE_NOW = ("red", [0.85, 0.10, 0.10])
_CHANGE_EARLIER = [
    ("blue",   [0.10, 0.30, 0.85]),
    ("green",  [0.10, 0.65, 0.20]),
    ("orange", [1.00, 0.55, 0.00]),
    ("purple", [0.55, 0.15, 0.75]),
    ("cyan",   [0.10, 0.80, 0.85]),
    ("yellow", [0.95, 0.85, 0.10]),
]


def change_color_plan(prior_states, target_step):
    """Colour each face of `target_step` by WHICH STEP introduced it.

    `prior_states` is the run's accepted-state chain, oldest first:
    `[input_step, accepted_1, accepted_2, ...]`. Every face of the target is
    matched (same `_face_key` tolerance as `new_face_region`) against each
    state in order; its origin is the first state that contains it.

      origin == 0        -> inherited from the original input -> gray
      origin == i (i>0)  -> introduced by accepted step i     -> its own colour
      origin is None     -> new or re-cut in THIS attempt     -> always red

    Returns a `color_plan`-style group list for the tagged renderer, or [] on
    any failure — a plain gray render beats no render, exactly as everywhere
    else in this file. A face-count cap is deliberate absent: the matching is
    O(faces) per state and the chain is a handful of states long.
    """
    try:
        t_faces = load_shape(target_step).Faces()
        if not t_faces:
            return []
        t_props = _face_props(t_faces)
        origin = [None] * len(t_props)
        for si, sp in enumerate(prior_states or []):
            pending = [i for i, o in enumerate(origin) if o is None]
            if not pending:
                break
            try:
                p_props = _face_props(load_shape(sp).Faces())
            except Exception:
                continue        # one unreadable state loses its colour, not the plan
            # Tolerant, so an untouched body that the attempt merely re-exported
            # stays gray instead of being painted as this attempt's edit.
            matched, _ = _match_face_sets(p_props, [t_props[i] for i in pending])
            for k in matched:
                origin[pending[k]] = si

        groups = []
        now_ids = [i for i, o in enumerate(origin) if o is None]
        if now_ids:
            name, rgb = _CHANGE_NOW
            groups.append({
                "name": name, "rgb": list(rgb),
                "label": "new or re-cut by THIS attempt (the edit being judged)",
                "face_ids": now_ids,
                "tags": "face_idx=" + _capped(now_ids, _PRINT_COLOR_IDX),
            })
        for si in range(len(prior_states or []) - 1, 0, -1):
            ids = [i for i, o in enumerate(origin) if o == si]
            if not ids:
                continue
            name, rgb = _CHANGE_EARLIER[(si - 1) % len(_CHANGE_EARLIER)]
            groups.append({
                "name": name, "rgb": list(rgb),
                "label": f"introduced by earlier accepted step {si} "
                         f"(already judged, kept)",
                "face_ids": ids,
                "tags": "face_idx=" + _capped(ids, _PRINT_COLOR_IDX),
            })
        if not groups:
            return []
        unchanged = sum(1 for o in origin if o == 0)
        groups.append({
            "name": GRAY_NAME, "rgb": list(GRAY_RGB),
            "label": f"unchanged from the original part "
                     f"({unchanged} of {len(t_props)} faces)",
            "face_ids": [],
            "tags": "face_idx=(everything not listed above)",
        })
        return groups
    except Exception:
        return []


# Above this many bodies the per-solid table costs more mass-property calls
# than it is worth, and is too long to read in a prompt.
_PER_SOLID_MAX = 24


def per_solid_delta(sa, sb):
    """Per-body volume change, bodies paired by centroid — or None.

    On a multi-body part "did the OTHER body change?" is the question QA is
    most often asked and least able to answer: the totals move either way, and
    the renders cannot separate a body that was edited from one that merely
    sits behind it. Measured: four correct attempts that cut a 36 mm^3 chamfer
    into a hub while leaving the 27,616 mm^3 spoked body alone (+0.07 mm^3,
    0.0003%) were each rejected for "unrequested edits on the main body",
    because nothing in front of QA distinguished the two.
    """
    try:
        solids_a, solids_b = sa.Solids(), sb.Solids()
        if not solids_a or not solids_b:
            return None
        if max(len(solids_a), len(solids_b)) > _PER_SOLID_MAX:
            return None
        info_a = [(s.Volume(), s.Center()) for s in solids_a]
        info_b = [(s.Volume(), s.Center()) for s in solids_b]
        taken, rows = set(), []
        # Pair on centroid distance AND volume similarity. Centroid alone is
        # unusable on a rotationally symmetric assembly: every blade of a rotor
        # has its centroid on the axis, so a boolean that shifts one body by a
        # micron silently permutes the whole table. Measured on task
        # F332D3FXML85WLR2_1769607142: s3 (72467.308 mm^3) moved 0.0009 mm and
        # was paired with s2 (25876.492 mm^3) instead of itself, which reported
        # "72467.308 -> 25876.492" and "25876.492 -> 69586.38" as if two bodies
        # had been rebuilt. QA rejected the attempt for a "scope violation"
        # that never happened. The same artifact produced three false
        # "non-target bodies were partially modified" verdicts on task
        # 3YH2WFSRM22W7DKT_1769779150, one of which discarded an edit that was
        # byte-for-byte the ground truth.
        #
        # A relative volume term breaks exactly that tie. It is ADDED, not
        # multiplied: on the rotor every candidate distance is ~0, and scaling
        # zero by anything is still zero. The scale is 1% of the part diagonal,
        # which is small enough that real distance still decides whenever the
        # candidates are meaningfully apart (a body that genuinely lost half
        # its volume still pairs with itself at d~0 rather than with a
        # same-sized body 100 mm away), and large enough to settle a tie
        # between two bodies sitting on the same axis.
        bb = sa.BoundingBox()
        tie_scale = 0.01 * max(
            _diagonal([bb.xlen, bb.ylen, bb.zlen]), _FRAME_TOL_MM)
        for va, ca in sorted(info_a, key=lambda t: -t[0]):
            best, best_d = None, None
            for j, (vb, cb) in enumerate(info_b):
                if j in taken:
                    continue
                d = ((ca.x - cb.x) ** 2 + (ca.y - cb.y) ** 2
                     + (ca.z - cb.z) ** 2) ** 0.5
                denom = max(abs(va), abs(vb), 1e-9)
                d += (abs(va - vb) / denom) * tie_scale   # 0 = identical size
                if best_d is None or d < best_d:
                    best, best_d = j, d
            if best is None:
                rows.append({"volume_before": _fmt(va, 3), "volume_after": None,
                             "delta": None, "pct": None})
                continue
            taken.add(best)
            vb = info_b[best][0]
            rows.append({
                "volume_before": _fmt(va, 3), "volume_after": _fmt(vb, 3),
                "delta": _fmt(vb - va, 3),
                "pct": _fmt(100.0 * (vb - va) / va, 4) if va else None,
            })
        for j, (vb, _) in enumerate(info_b):
            if j not in taken:
                rows.append({"volume_before": None, "volume_after": _fmt(vb, 3),
                             "delta": _fmt(vb, 3), "pct": None})
        return rows if len(rows) > 1 else None
    except Exception:
        return None


def compare(before_step, after_step):
    """Geometric diff between two STEPs — the objective feedback the QA agent
    gets alongside the pictures, so acceptance is not purely visual."""
    sa, sb = load_shape(before_step), load_shape(after_step)
    a = _summarize_shape(sa, osp.basename(str(before_step)))
    b = _summarize_shape(sb, osp.basename(str(after_step)))
    va, vb = a["volume_mm3"], b["volume_mm3"]
    dv = (vb - va) if va is not None and vb is not None else None
    out = {
        "solids": [a["solids"], b["solids"], b["solids"] - a["solids"]],
        "faces": [a["faces"], b["faces"], b["faces"] - a["faces"]],
        "edges": [a["edges"], b["edges"], b["edges"] - a["edges"]],
        "volume_mm3": [va, vb, _fmt(dv, 4) if dv is not None else None],
        "volume_change_pct": _fmt(100.0 * dv / va, 3) if dv is not None and va else None,
        "bbox_before": a["bbox_size"],
        "bbox_after": b["bbox_size"],
        "bbox_changed": a["bbox_size"] != b["bbox_size"],
        "bbox_min_before": a["bbox_min"],
        "bbox_max_before": a["bbox_max"],
        "bbox_min_after": b["bbox_min"],
        "bbox_max_after": b["bbox_max"],
        "per_solid": per_solid_delta(sa, sb),
        "envelope_growth": envelope_growth(a, b),
        "frame_drift": frame_drift(a, b),
        # Topology moved but no material did: a cut that missed the solid, or a
        # boolean that only imprinted edges. Distinct from a true no-op (which
        # leaves the face count alone too) and just as worthless — measured on
        # a rotor run, an attempt added 9 faces for a 0.0 mm^3 volume delta and
        # still cost a full QA call to discover it.
        # The percentage is relative to the WHOLE part, so on a large body a
        # real feature disappears into it: a 52.0 mm^3 fillet on a 127,190 mm^3
        # mouse shell is 0.041%, under the 0.05% tolerance. It was reported as
        # "topology changed with 0 volume delta — correct for merge/fuse
        # goals", QA accepted at 0.96 confidence, and the edit had in fact
        # rounded the WRONG rim (ground truth +2.87 mm^3, ours -52.02). Hence
        # the absolute floor as well, the same one `is_noop` already applies:
        # material that moved in mm^3 has moved, whatever share of the part it
        # represents.
        "no_material_change": (
            a["bbox_size"] == b["bbox_size"]
            and (b["faces"] != a["faces"] or b["edges"] != a["edges"])
            and a["volume_mm3"]
            and abs(100.0 * (b["volume_mm3"] - a["volume_mm3"]) / a["volume_mm3"])
            < _ROUNDTRIP_VOLUME_TOL_PCT
            and abs(b["volume_mm3"] - a["volume_mm3"])
            < max(_NOOP_MAX_ABS_MM3,
                  _NOOP_REL_FRAC * abs(a["volume_mm3"]))),
        "surface_types_before": a["surface_types"],
        "surface_types_after": b["surface_types"],
        "new_surface_types": sorted(
            set(b["surface_types"]) - set(a["surface_types"])),
        "still_valid": b["valid"],
        # The BEFORE validity too, so a judge can tell "this edit broke the
        # solid" from "the solid arrived broken". The router already makes that
        # distinction and forgives a pre-existing invalidity; QA was handed only
        # the after-value and led four of five verdicts on task
        # SUJ2G2UMJQR7PMBX_1759203739 with "Resulting model is not a valid
        # solid" about a defect the edit did not cause.
        "was_valid": a["valid"],
    }
    # Filled in after the dict exists so is_noop() can reuse these numbers.
    out["identical"] = is_noop(before_step, after_step, out)
    # Where the new material landed. Reuses the two shapes already loaded above,
    # so this adds one mass-property pass per face and no STEP import (measured:
    # +5.4 s on the 799-face benchmark input, +0.4 s on the 266-face bracket).
    # Skipped outright on a no-op, which by definition has no new geometry and
    # is the single commonest outcome of a failed attempt.
    out["new_region"] = (None if out["identical"] else
                         new_face_region(before_step, after_step, _shapes=(sa, sb)))
    # Phantom material: a lot more summed volume, the same envelope. Fusing ~12
    # imported solids costs seconds, so it is only paid on that exact signature.
    out["phantom_material"] = None
    pct = out["volume_change_pct"]
    if (dv is not None and dv > 0 and pct is not None
            and pct > _PHANTOM_VOLUME_RISE_PCT
            and not out["bbox_changed"]
            and not out["envelope_growth"]):
        oa, ob = occupied_volume(before_step), occupied_volume(after_step)
        if oa is not None and ob is not None:
            do = ob - oa
            if do < _PHANTOM_OCCUPIED_FRAC * dv:
                out["phantom_material"] = (
                    f"summed volume rose {_fmt(dv, 1)} mm^3 but occupied space "
                    f"rose only {_fmt(do, 1)} mm^3 — the new body lies (almost) "
                    f"entirely inside existing material")
    return out


# Operations whose SIGN is fixed by definition. Fillet/chamfer is deliberately
# absent: blending a CONVEX edge removes material and a CONCAVE one adds it, so
# its sign is a property of the edge, not of the operation, and asserting one
# would reject correct work.
_TAG_EXPECTED_SIGN = {"cut-hole-slot": -1, "add-body": +1}

# Tags whose sign is undefined by their OWN definition, so a sub-goal carrying
# one has no expected sign no matter what else it is tagged with. `profile-swap`
# is documented in tools/skillref.py as "replace a feature's cross-section with
# a different one (round to hex, square to round)" — a hex inscribed in the old
# circle removes material and a circumscribed one adds it, which is the same
# argument the comment above already makes for fillet/chamfer.
#
# Measured on task 21 (SUJ2G2UMJQR7PMBX_1759210049, "change the center flower
# profile into a hexagonal profile"): a replan added `cut-hole-slot` to a
# `profile-swap` sub-goal, and attempts 4 and 5 were then rejected on the sign
# with no QA call. Attempt 5 was tied for the best geometry the run produced
# (diff_f1 0.3554); the run shipped 0.0.
#
# NOTE: `resize-feature` ("bigger, smaller, deeper, longer, thicker") is
# arguably in the same position, but no run has been measured losing an attempt
# to it, so it is deliberately left out rather than widened on theory.
_TAG_SIGN_UNDEFINED = {"profile-swap", "mirror-pattern"}
# `mirror-pattern` is documented as "duplicate existing geometry — mirror it,
# or repeat it", so it is inherently ADDITIVE; paired with `cut-hole-slot` the
# net sign is undefined. Measured on task 3YH2WFSRM22W7DKT_1769779150, whose
# sub-goal was "duplicate the existing black U-shaped stand ... Trim only the
# copied stand below Z=-115" — necessarily net-additive, and rejected for
# ADDING 2.063e+05 mm^3 under `cut-hole-slot`. It cost two attempts and one
# of the two replans, and the rejected attempt scored diff_f1 0.7911 against
# the 0.7356 that eventually shipped.

# Below this the delta is kernel round-trip noise and the sign means nothing.
_SIGN_MIN_ABS_MM3 = 1.0


def volume_direction_conflict(diff, tags):
    """"Cut" that added material, or "add" that removed it — message or None.

    The cheapest possible check on whether an attempt did the KIND of thing it
    was asked to do, and one nothing else performs: volume magnitude gates ask
    "did anything move", the envelope gate asks "did the outside move", and QA
    reads pictures in which a rounded rim and a chamfered rim look alike.
    Measured: a fillet sub-goal shipped -52.02 mm^3 where the human's edit was
    +2.87 mm^3 — the opposite rim of the same slot, 18x the magnitude — and was
    accepted, scoring 0.066 where a single-shot baseline scored 0.995.
    """
    try:
        dv = float((diff.get("volume_mm3") or [0, 0, 0])[2] or 0.0)
    except (TypeError, ValueError):
        return None
    if abs(dv) < _SIGN_MIN_ABS_MM3:
        return None
    # A sub-goal tagged BOTH `add-body` and `cut-hole-slot` cannot satisfy this
    # gate at all: a negative delta violates the first, a positive one violates
    # the second, and only a delta below the noise floor slips through. Every
    # attempt is rejected on the sign, unseen by QA, whatever it builds.
    #
    # That tag pair is not a strategist blunder — it is the natural
    # decomposition of "add a switch/boss/port", where one step both cuts a
    # recess and adds the part sitting in it. Measured on task 20
    # (4S7JQK6ZQMAD25GL_1758863403, "add a sliding switch"): attempts 1 and 2
    # were rejected against `add-body`, attempt 3 against `cut-hole-slot` —
    # three executor calls and ~490 s of a 1359 s run spent without QA being
    # consulted once. The replan that eventually fixed it did nothing but split
    # the tags into a cut sub-goal and an add sub-goal, after which both passed
    # on the next real attempt. 5 of 39 run logs carry such a sub-goal.
    #
    # Splitting the plan is the better fix and belongs in the strategist. What
    # this gate must not do meanwhile is enforce a contradiction: an impossible
    # question abstains rather than rejecting every possible answer.
    if any(t in _TAG_SIGN_UNDEFINED for t in (tags or [])):
        return None
    signs = {_TAG_EXPECTED_SIGN[t] for t in (tags or [])
             if t in _TAG_EXPECTED_SIGN}
    if len(signs) > 1:
        return None
    for tag in tags or []:
        want = _TAG_EXPECTED_SIGN.get(tag)
        if want and (dv > 0) != (want > 0):
            did, expected = ("ADDED", "remove") if dv > 0 else ("REMOVED", "add")
            return (f"the sub-goal is tagged `{tag}`, which can only {expected} "
                    f"material, but this attempt {did} {abs(dv):.4g} mm^3")
    return None


def envelope_text(diff):
    """The frame/envelope part of a diff, as the lines shown to the agents."""
    g = diff.get("envelope_growth") or {}
    grew = ", ".join(f"{k} by {v} mm" for k, v in sorted(g.items())) or "unchanged"
    lines = [
        f"  bbox before: min {diff.get('bbox_min_before')} "
        f"max {diff.get('bbox_max_before')}",
        f"  bbox after:  min {diff.get('bbox_min_after')} "
        f"max {diff.get('bbox_max_after')}",
        f"  outer envelope grew: {grew}",
    ]
    ps = diff.get("per_solid")
    if ps:
        lines.append("  PER-BODY VOLUME (largest first; this is how you tell "
                     "WHICH body was edited):")
        for n, r in enumerate(ps):
            pct = f" ({r['pct']}%)" if r.get("pct") is not None else ""
            lines.append(f"    body {n}: {r.get('volume_before')} -> "
                         f"{r.get('volume_after')}  delta {r.get('delta')}{pct}")
    if diff.get("frame_drift"):
        lines.append(f"  !! FRAME DRIFT: {diff['frame_drift']}")
    if diff.get("phantom_material"):
        lines.append(f"  !! PHANTOM MATERIAL: {diff['phantom_material']}")
    nr = diff.get("new_region")
    if nr:
        c = nr["centroid"]
        lines.append(
            f"  NEW GEOMETRY LANDED AT: centroid ({c[0]}, {c[1]}, {c[2]})  "
            f"bbox min {nr['bbox_min']} max {nr['bbox_max']}  "
            f"({nr['n_new']} new faces)")
    return "\n".join(lines)


TOOL_DOC = """\
You can rely on a pre-computed geometry index of the input solid (provided in
the prompt as `geometry`). It contains:

  summary                 face/edge/vertex counts, surface-type mix, bbox, volume
  circular_edge_groups    EVERY circular edge of the solid, grouped by radius -> use
                          this to find "the hole edges"; each group gives radius,
                          diameter, count, and per-edge centers and edge indices
  cylindrical_face_groups every cylindrical face by radius -> hole walls / bosses
  planar_faces            planar faces sorted by area, each with outward normal,
                          dominant axis, and a `vertical` flag

Use real numbers from this index (radii, coordinates, z-levels) to build CadQuery
selectors instead of guessing. Selecting by measured position/size is far more
reliable than string selectors on an imported B-rep with no feature tree.

The index is complete — no family is withheld for being too small, too large or
judged irrelevant. The [scale] tag on a family (feature / small / blend /
oversize) is an ordering hint only: a 0.4 mm chamfer ring is tagged "blend" and
is still the right target when the instruction asks for a chamfer. Where a long
list is cut for length it says so inline, e.g. "(showing 6 of 32)"; a list
without that marker is all of them.
"""
