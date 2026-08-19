def my_cad_function(args):
    import cadquery as cq
    import math
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"INPUT: solids={len(solids)} faces={len(base.Faces())} edges={len(base.Edges())} verts={len(base.Vertices())}")
    bb0 = base.BoundingBox()
    print(
        "INPUT BBOX:",
        f"xmin={bb0.xmin:.6f} xmax={bb0.xmax:.6f} ymin={bb0.ymin:.6f} ymax={bb0.ymax:.6f} zmin={bb0.zmin:.6f} zmax={bb0.zmax:.6f}",
    )

    if len(solids) != 1:
        print("ERROR: Expected exactly 1 solid; returning input unchanged")
        return base

    s0 = solids[0]

    # --- Helpers ---
    def vtuple(v):
        return [float(v.x), float(v.y), float(v.z)]

    def dist(a: cq.Vector, b: cq.Vector) -> float:
        d = a - b
        return float(d.Length)

    def edge_geom_info(e: cq.Edge):
        gt = None
        try:
            gt = e.geomType()
        except Exception:
            gt = "UNKNOWN"

        rad = None
        if gt == "CIRCLE":
            try:
                circ = BRepAdaptor_Curve(e.wrapped).Circle()
                rad = float(circ.Radius())
            except Exception:
                rad = None
        return gt, rad

    def best_match_edge(cur_solid: cq.Solid, orig):
        """Find an edge on cur_solid corresponding to an original edge record."""
        # Tight tolerance to avoid newly-created fillet edges (offset by ~r)
        tol_line_mid = 0.12
        tol_circle_mid = 0.75
        tol_circle_r = 0.06

        cur_edges = cur_solid.Edges()
        candidates = []

        for j, ce in enumerate(cur_edges):
            gt, rad = edge_geom_info(ce)
            if gt != orig["geom"]:
                continue

            cm = ce.positionAt(0.5)
            dmid = dist(cm, orig["mid"])

            if gt == "LINE":
                if dmid > tol_line_mid:
                    continue
                # length check to reduce chance of picking new tangent lines
                try:
                    clen = float(ce.Length())
                except Exception:
                    continue
                # allow some shortening at ends due to neighboring fillets
                if abs(clen - orig["len"]) > max(0.35, 0.35 * orig["len"]):
                    continue
                candidates.append((dmid, abs(clen - orig["len"]), j, ce))

            elif gt == "CIRCLE":
                if orig["rad"] is None or rad is None:
                    continue
                if abs(rad - orig["rad"]) > tol_circle_r:
                    continue
                if dmid > tol_circle_mid:
                    continue
                try:
                    clen = float(ce.Length())
                except Exception:
                    clen = orig["len"]
                candidates.append((dmid, abs(rad - orig["rad"]), j, ce))

        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates

    def try_fillet_list(cur_solid, r, edge_list, label):
        if not edge_list:
            print(f"SELECTED: 0 edges for {label}")
            return cur_solid, 0, True
        idxs = []
        # best-effort index in current edge list for reporting
        cur_edges = cur_solid.Edges()
        h2i = {e.hashCode(): i for i, e in enumerate(cur_edges)}
        for e in edge_list:
            try:
                idxs.append(h2i.get(e.hashCode(), None))
            except Exception:
                idxs.append(None)
        print(f"SELECTED: {len(edge_list)} edges for {label} idx={idxs}")
        try:
            out = cur_solid.fillet(r, edge_list)
            return out, len(edge_list), True
        except Exception as e:
            print(f"WARN: batch fillet failed for {label}: {repr(e)}")
            return cur_solid, 0, False

    # --- Index sanity prints (faces/edges mentioned) ---
    try:
        f0 = base.Faces()[0]
        f14 = base.Faces()[14]
        print(
            "CHECK face#0:",
            f"area={f0.Area():.6f}",
            f"center={[round(c, 6) for c in f0.Center().toTuple()]}",
        )
        print(
            "CHECK face#14:",
            f"area={f14.Area():.6f}",
            f"center={[round(c, 6) for c in f14.Center().toTuple()]}",
        )
    except Exception as e:
        print("WARN: Face index sanity check failed:", repr(e))

    orig_edges = s0.Edges()
    print(f"SOLID0: faces={len(s0.Faces())} edges={len(orig_edges)} verts={len(s0.Vertices())}")

    # Record original edge descriptors (for non-recursive matching)
    originals = []
    for i, e in enumerate(orig_edges):
        gt, rad = edge_geom_info(e)
        mid = e.positionAt(0.5)
        try:
            elen = float(e.Length())
        except Exception:
            elen = None
        originals.append({
            "i": i,
            "geom": gt,
            "rad": rad,
            "mid": mid,
            "len": elen if elen is not None else 0.0,
        })

    # Explicit checks for required curved boundary edges
    for i in [0, 2, 19, 21]:
        if i < len(orig_edges):
            e = orig_edges[i]
            gt, rad = edge_geom_info(e)
            c = e.Center()
            m = e.positionAt(0.5)
            print(
                f"CHECK edge_idx[{i}]: geom={gt} rad={None if rad is None else round(rad,6)} ",
                f"len={e.Length():.6f} center={[round(c.x,6), round(c.y,6), round(c.z,6)]} mid={[round(m.x,6), round(m.y,6), round(m.z,6)]}",
            )
        else:
            print(f"CHECK edge_idx[{i}]: OUT OF RANGE (edges={len(orig_edges)})")

    # --- Fillet plan ---
    r = 0.2
    print("TARGET FILLET RADIUS:", r)

    # Split originals into two groups: LINE edges and BIG CIRCLE edges (exclude any ~r fillet edges by design)
    line_orig = [o for o in originals if o["geom"] == "LINE"]
    circ_orig = [o for o in originals if o["geom"] == "CIRCLE" and (o["rad"] is not None and o["rad"] > 1.0)]
    other_orig = [o for o in originals if o not in line_orig and o not in circ_orig]

    print(f"ORIGINAL EDGE TYPES: LINE={len(line_orig)} BIG_CIRCLE={len(circ_orig)} OTHER={len(other_orig)} (total={len(originals)})")

    current = s0
    vol0 = float(current.Volume())

    # 1) Try batch fillet all LINE edges first (on original solid, so no recursion possible)
    line_edges_now = [orig_edges[o["i"]] for o in line_orig]
    current, applied, ok = try_fillet_list(current, r, line_edges_now, "batch fillet on ORIGINAL LINE edges")
    if ok and applied > 0:
        print(f"APPLIED: {applied} line-edge fillets in one batch")

    # If batch failed, do per-original-edge fillets for LINE edges using matching against originals
    if not ok:
        print("FALLBACK: per-edge fillet for ORIGINAL LINE edges (matching by midpoint/length; avoiding new fillet edges)")
        done = 0
        for o in line_orig:
            cands = best_match_edge(current, o)
            if not cands:
                print(f"SELECTED: 0 matching edges for original LINE edge_idx[{o['i']}] midpoint={[round(x,6) for x in vtuple(o['mid'])]}")
                continue
            # try up to 3 closest matches
            tried = 0
            success = False
            for dmid, daux, j, ce in cands[:3]:
                tried += 1
                print(f"SELECTED: 1 edge for fillet original LINE edge_idx[{o['i']}] -> current_edge_idx[{j}] dmid={dmid:.6f} dlen={daux:.6f}")
                try:
                    before = current
                    current = current.fillet(r, [ce])
                    dv = float(before.Volume() - current.Volume())
                    print(f"FILLET OK: edge_idx[{o['i']}] removedVol={dv:.9f}")
                    done += 1
                    success = True
                    break
                except Exception as e:
                    print(f"WARN: fillet failed on candidate current_edge_idx[{j}] for original edge_idx[{o['i']}]: {repr(e)}")
            if not success:
                print(f"WARN: all candidates failed for original LINE edge_idx[{o['i']}] (tried {tried})")
        print(f"FALLBACK RESULT: succeeded on {done}/{len(line_orig)} original LINE edges")

    # 2) Now fillet BIG CIRCLE edges (use matching on current solid to avoid selecting new r=0.2 edges)
    print("STEP: fillet ORIGINAL BIG-CIRCLE boundary edges (including required edge_idx [0,2,19,21])")

    # Try a batch on currently matched big-circle edges
    matched_circ_edges = []
    matched_map = []
    for o in circ_orig:
        cands = best_match_edge(current, o)
        if not cands:
            continue
        dmid, daux, j, ce = cands[0]
        matched_circ_edges.append(ce)
        matched_map.append((o["i"], j, dmid, daux))

    print(f"SELECTED: {len(matched_circ_edges)} edges for batch fillet on BIG_CIRCLE (matched from originals)")
    if matched_map:
        print("MATCH MAP (orig_idx -> cur_idx):", [(a, b) for a, b, _, _ in matched_map])

    current2, applied2, ok2 = try_fillet_list(current, r, matched_circ_edges, "batch fillet on matched ORIGINAL BIG_CIRCLE edges")
    if ok2 and applied2 > 0:
        current = current2
        print(f"APPLIED: {applied2} big-circle-edge fillets in one batch")

    # If circle batch failed, do per-edge fillets for circles
    if (not ok2) and circ_orig:
        print("FALLBACK: per-edge fillet for ORIGINAL BIG_CIRCLE edges (matching by circle radius and midpoint)")
        done = 0
        for o in circ_orig:
            cands = best_match_edge(current, o)
            if not cands:
                print(f"SELECTED: 0 matching edges for original CIRCLE edge_idx[{o['i']}] r={o['rad']}")
                continue
            tried = 0
            success = False
            for dmid, drad, j, ce in cands[:3]:
                tried += 1
                print(f"SELECTED: 1 edge for fillet original CIRCLE edge_idx[{o['i']}] -> current_edge_idx[{j}] dmid={dmid:.6f} drad={drad:.6f}")
                try:
                    before = current
                    current = current.fillet(r, [ce])
                    dv = float(before.Volume() - current.Volume())
                    print(f"FILLET OK: edge_idx[{o['i']}] removedVol={dv:.9f}")
                    done += 1
                    success = True
                    break
                except Exception as e:
                    print(f"WARN: fillet failed on candidate current_edge_idx[{j}] for original CIRCLE edge_idx[{o['i']}]: {repr(e)}")
            if not success:
                print(f"WARN: all candidates failed for original CIRCLE edge_idx[{o['i']}] (tried {tried})")
        print(f"FALLBACK RESULT: succeeded on {done}/{len(circ_orig)} original BIG_CIRCLE edges")

    # 3) If there are any OTHER original edge types, attempt them carefully (rare for this part)
    if other_orig:
        print("STEP: attempt fillet on OTHER original edge types (if any)")
        done = 0
        for o in other_orig:
            cands = best_match_edge(current, o)
            if not cands:
                print(f"SELECTED: 0 matching edges for original OTHER edge_idx[{o['i']}] geom={o['geom']}")
                continue
            dmid, daux, j, ce = cands[0]
            print(f"SELECTED: 1 edge for fillet original OTHER edge_idx[{o['i']}] -> current_edge_idx[{j}] dmid={dmid:.6f}")
            try:
                before = current
                current = current.fillet(r, [ce])
                dv = float(before.Volume() - current.Volume())
                print(f"FILLET OK: edge_idx[{o['i']}] removedVol={dv:.9f}")
                done += 1
            except Exception as e:
                print(f"WARN: fillet failed for original OTHER edge_idx[{o['i']}]: {repr(e)}")
        print(f"OTHER RESULT: succeeded on {done}/{len(other_orig)}")

    out_solid = current

    # --- Verification prints ---
    out_solids = out_solid.Solids()
    print(f"OUTPUT: solids={len(out_solids)} faces={len(out_solid.Faces())} edges={len(out_solid.Edges())} verts={len(out_solid.Vertices())}")
    bb1 = out_solid.BoundingBox()
    print(
        "OUTPUT BBOX:",
        f"xmin={bb1.xmin:.6f} xmax={bb1.xmax:.6f} ymin={bb1.ymin:.6f} ymax={bb1.ymax:.6f} zmin={bb1.zmin:.6f} zmax={bb1.zmax:.6f}",
    )

    tgt = {"xmin": -1.0, "xmax": 1.0, "ymin": -3.0, "ymax": 3.0, "zmin": -0.75, "zmax": 0.75}
    deltas = {
        "xmin": bb1.xmin - tgt["xmin"],
        "xmax": bb1.xmax - tgt["xmax"],
        "ymin": bb1.ymin - tgt["ymin"],
        "ymax": bb1.ymax - tgt["ymax"],
        "zmin": bb1.zmin - tgt["zmin"],
        "zmax": bb1.zmax - tgt["zmax"],
    }
    print("BBOX DELTAS vs target:", {k: round(v, 9) for k, v in deltas.items()})

    vol1 = float(out_solid.Volume())
    print(f"VOLUME: before={vol0:.9f} after={vol1:.9f} delta={vol1 - vol0:.9f}")

    # Count small-radius circle edges ~0.2 as evidence of R fillets
    small_circles = []
    for idx, e in enumerate(out_solid.Edges()):
        gt, rad = edge_geom_info(e)
        if gt == "CIRCLE" and rad is not None and abs(rad - r) < 0.03:
            small_circles.append(idx)
    print(f"CHECK: edges with CIRCLE radius ~{r}: count={len(small_circles)} (sample idx={small_circles[:20]})")

    if len(out_solids) != 1:
        print("ERROR: Result is not exactly one solid; returning best-effort modified shape anyway")

    return out_solid