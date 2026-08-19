def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    if len(sols) != 1:
        print(f"WARNING: expected 1 solid, found {len(sols)}; editing solid[0] only")
    solid = sols[0]

    # --- Resolve/verify the referenced face and edges from the provided geometry index ---
    faces = solid.Faces()
    edges = solid.Edges()

    # Face #10: planar face at Z=-340
    try:
        f10 = faces[10]
        c10 = f10.Center()
        a10 = f10.Area()
        n10 = f10.normalAt()  # no args
        print(f"FACE#10: area={a10:.3f} center={[round(c10.x,3), round(c10.y,3), round(c10.z,3)]} normal={[round(n10.x,3), round(n10.y,3), round(n10.z,3)]}")
    except Exception as e:
        print(f"ERROR: could not resolve face #10: {e}")
        return shape

    # Target edges by explicit edge_idx list
    target_edge_idx = [39, 41, 43, 45]
    picked = []
    picked_idx = []
    ref_pts = []

    # region limits
    x0, x1 = 0.0, 100.0
    y0, y1 = 200.0, 320.0
    z_plane = -340.0

    print("NAMED NUMBERS: z_plane=-340.0, region X=0..100, Y=200..320, fillet_r=5.0")

    for i in target_edge_idx:
        if i < 0 or i >= len(edges):
            print(f"EDGE#{i}: out of range (0..{len(edges)-1})")
            continue
        e = edges[i]
        bb = e.BoundingBox()
        ec = e.Center()
        on_z340 = (abs(bb.zmin - z_plane) < 1e-3 and abs(bb.zmax - z_plane) < 1e-3)
        in_region = (bb.xmax >= x0 and bb.xmin <= x1 and bb.ymax >= y0 and bb.ymin <= y1)
        print(
            f"EDGE#{i}: len={e.Length():.3f} center={[round(ec.x,3), round(ec.y,3), round(ec.z,3)]} "
            f"bb=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f})-({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}) "
            f"on_z340={on_z340} in_regionXY={in_region}"
        )
        # Keep only edges in the requested region on the requested plane
        if on_z340 and in_region:
            picked.append(e)
            picked_idx.append(i)
            ref_pts.append((bb.center.x, bb.center.y, bb.center.z))

    print(f"SELECTED: {len(picked)} edges for r=5.0 fillet on bulb perimeter @ face#10  idx={picked_idx}")
    if len(picked) == 0:
        print("SELECTED: 0 edges -> NO-OP (refusing to silently return unchanged solid)")
        return shape

    # --- Apply fillet ---
    fillet_r = 5.0

    def _try_fillet_all(s, elist):
        try:
            out = s.fillet(fillet_r, elist)
            return out, True
        except Exception as e:
            print(f"FILLET(all-at-once) FAILED: {e}")
            return s, False

    def _closest_edge_on_plane(s, pt, z=z_plane, xlim=(x0, x1), ylim=(y0, y1), ztol=0.25):
        tx, ty, tz = pt
        best = None
        best_i = None
        best_d2 = 1e99
        for j, ee in enumerate(s.Edges()):
            bb = ee.BoundingBox()
            if abs(bb.zmin - z) > ztol or abs(bb.zmax - z) > ztol:
                continue
            if bb.xmax < xlim[0] or bb.xmin > xlim[1] or bb.ymax < ylim[0] or bb.ymin > ylim[1]:
                continue
            cc = ee.Center()
            d2 = (cc.x - tx) ** 2 + (cc.y - ty) ** 2 + (cc.z - tz) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best = ee
                best_i = j
        return best, best_i, best_d2

    out_solid, ok = _try_fillet_all(solid, picked)

    if not ok:
        # Fallback: fillet iteratively, re-finding the closest edge after each modification
        out_solid = solid
        successes = 0
        for k, pt in enumerate(ref_pts):
            ee, jj, d2 = _closest_edge_on_plane(out_solid, pt)
            if ee is None:
                print(f"FALLBACK: could not re-find edge near ref_pt#{k}={pt} on z=-340 in region")
                continue
            print(f"FALLBACK SELECTED: 1 edge (current idx={jj}) near ref_pt#{k}={tuple(round(v,3) for v in pt)} d={d2**0.5:.3f}mm")
            try:
                out_solid = out_solid.fillet(fillet_r, [ee])
                successes += 1
            except Exception as e:
                print(f"FALLBACK FILLET FAILED on ref_pt#{k}: {e}")
        print(f"FALLBACK RESULT: {successes}/{len(ref_pts)} edges filleted")

    # --- Self-checks: bbox must remain unchanged ---
    bb0 = solid.BoundingBox()
    bb1 = out_solid.BoundingBox()
    print(
        "BBOX BEFORE: "
        f"min={[round(bb0.xmin,3), round(bb0.ymin,3), round(bb0.zmin,3)]} "
        f"max={[round(bb0.xmax,3), round(bb0.ymax,3), round(bb0.zmax,3)]}"
    )
    print(
        "BBOX AFTER : "
        f"min={[round(bb1.xmin,3), round(bb1.ymin,3), round(bb1.zmin,3)]} "
        f"max={[round(bb1.xmax,3), round(bb1.ymax,3), round(bb1.zmax,3)]}"
    )
    print(
        "BBOX DELTA : "
        f"dmin={[round(bb1.xmin-bb0.xmin,6), round(bb1.ymin-bb0.ymin,6), round(bb1.zmin-bb0.zmin,6)]} "
        f"dmax={[round(bb1.xmax-bb0.xmax,6), round(bb1.ymax-bb0.ymax,6), round(bb1.zmax-bb0.zmax,6)]}"
    )

    # Return edited solid (single-body file)
    return out_solid