def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    faces = base.Faces()
    edges = base.Edges()
    solids = base.Solids()
    print(f"INFO: base faces={len(faces)} edges={len(edges)} solids={len(solids)}")

    # --- Sub-goal absolute targets ---
    x0, y0 = -37.304, 1.753
    head_r = 4.0   # 8.0 mm diameter
    head_h = 1.2
    z_low0, z_low1 = -27.496, -26.296
    z_up0, z_up1 = 27.304, 28.504

    print("TARGET NUMBERS:")
    print(f"  pin axis: [0,0,1]")
    print(f"  center XY = ({x0:.3f}, {y0:.3f})")
    print(f"  head diameter = {2*head_r:.3f} mm")
    print(f"  head thickness = {head_h:.3f} mm")
    print(f"  lower head Z = {z_low0:.3f}..{z_low1:.3f}")
    print(f"  upper head Z = {z_up0:.3f}..{z_up1:.3f}")

    # --- Resolve referenced entities (global indices) ---
    f_idx = 38
    f38 = None
    if f_idx < 0 or f_idx >= len(faces):
        print(f"SELECTED: 0 faces for pin shaft (face #{f_idx} out of range) -- NO-OP")
        return shape

    f38 = faces[f_idx]
    c38 = f38.Center()
    gt = None
    rad = None
    try:
        gt = f38.geomType()
    except Exception:
        gt = "(unknown)"
    try:
        rad = f38.radius()
    except Exception:
        rad = None
    print(
        f"RESOLVED: face #{f_idx} geomType={gt} center=({c38.x:.3f},{c38.y:.3f},{c38.z:.3f})"
        + (f" radius={rad:.3f}" if isinstance(rad, (int, float)) else "")
        + f"  (expected ~c=({x0:.3f},{y0:.3f},*) r~2.4 axis~Z)"
    )

    # Resolve end edges for diagnostics
    end_edge_idx = [76, 82]
    got_edges = []
    for ei in end_edge_idx:
        if 0 <= ei < len(edges):
            e = edges[ei]
            got_edges.append(e)
            ec = e.Center()
            et = None
            try:
                et = e.geomType()
            except Exception:
                et = "(unknown)"
            ebb = e.BoundingBox()
            print(
                f"RESOLVED: edge #{ei} geomType={et} center=({ec.x:.3f},{ec.y:.3f},{ec.z:.3f}) "
                f"bboxZ={ebb.zmin:.3f}..{ebb.zmax:.3f}"
            )
        else:
            print(f"RESOLVED: edge #{ei} NOT FOUND (out of range)")
    print(f"SELECTED: {len(got_edges)} edges for pin end diagnostics idx={end_edge_idx}")

    # --- Find which solid owns face #38 so we only edit that one ---
    owner_idx = None
    for si, s in enumerate(solids):
        try:
            for sf in s.Faces():
                if sf.isSame(f38):
                    owner_idx = si
                    break
        except Exception:
            pass
        if owner_idx is not None:
            break

    if owner_idx is None:
        print("SELECTED: 0 solids owning face #38 -- NO-OP")
        return shape

    s_owner = solids[owner_idx]
    bb_owner = s_owner.BoundingBox()
    print(
        f"SELECTED: 1 solid owning face #{f_idx} (edit target) solid_idx={owner_idx} "
        f"bbox=([{bb_owner.xmin:.3f},{bb_owner.ymin:.3f},{bb_owner.zmin:.3f}].."
        f"[{bb_owner.xmax:.3f},{bb_owner.ymax:.3f},{bb_owner.zmax:.3f}])"
    )

    # --- Build heads at absolute coordinates ---
    def build_head(z0, h):
        pl = cq.Plane(origin=(x0, y0, z0), normal=(0, 0, 1))
        print(f"PLANE: origin=({x0:.3f},{y0:.3f},{z0:.3f}) normal=(0,0,1)")
        return cq.Workplane(pl).circle(head_r).extrude(h).val()

    head_low = build_head(z_low0, head_h)
    head_up = build_head(z_up0, head_h)

    def report_head(name, hd, zt0, zt1):
        c = hd.Center()
        bb = hd.BoundingBox()
        print(
            f"BUILT: {name} head center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) "
            f"bboxZ={bb.zmin:.3f}..{bb.zmax:.3f} "
            f"dXY=({c.x-x0:+.3f},{c.y-y0:+.3f}) "
            f"dZext=({bb.zmin-zt0:+.3f},{bb.zmax-zt1:+.3f})"
        )

    report_head("lower", head_low, z_low0, z_low1)
    report_head("upper", head_up, z_up0, z_up1)

    # --- Correct displacement if any (same attempt) ---
    def corrected(hd, target_x, target_y, target_zmin, target_zmax, tol=1e-3):
        bb = hd.BoundingBox()
        c = hd.Center()
        dx, dy = target_x - c.x, target_y - c.y
        dz = target_zmin - bb.zmin
        zmax_err = bb.zmax - target_zmax
        if abs(dx) > tol or abs(dy) > tol or abs(dz) > tol or abs(zmax_err) > tol:
            print(
                f"CORRECTING: translate ({dx:+.3f},{dy:+.3f},{dz:+.3f}) "
                f"to hit XY=({target_x:.3f},{target_y:.3f}) and Z={target_zmin:.3f}..{target_zmax:.3f}"
            )
            hd2 = hd.translate((dx, dy, dz))
            bb2 = hd2.BoundingBox()
            c2 = hd2.Center()
            print(
                f"CORRECTED: center=({c2.x:.3f},{c2.y:.3f},{c2.z:.3f}) "
                f"bboxZ={bb2.zmin:.3f}..{bb2.zmax:.3f}"
            )
            return hd2
        return hd

    head_low = corrected(head_low, x0, y0, z_low0, z_low1)
    head_up = corrected(head_up, x0, y0, z_up0, z_up1)

    # --- Fuse onto the owning solid only ---
    edited_owner = s_owner.fuse(head_low).fuse(head_up)
    print("BOOLEAN: fused 2 heads onto target solid")

    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != owner_idx] + [edited_owner])

    # --- Placement self-check: isolate added material and report ---
    try:
        added = out.cut(base)
        bbA = added.BoundingBox()
        cA = added.Center()
        print(
            f"ADDED (out.cut(base)) center=({cA.x:.3f},{cA.y:.3f},{cA.z:.3f}) "
            f"bbox=([{bbA.xmin:.3f},{bbA.ymin:.3f},{bbA.zmin:.3f}]..[{bbA.xmax:.3f},{bbA.ymax:.3f},{bbA.zmax:.3f}])"
        )
        print(
            "CHECK: added XY center vs target and Z mins/maxs vs targets: "
            f"dX={cA.x-x0:+.3f} dY={cA.y-y0:+.3f} "
            f"zmin {bbA.zmin:.3f} (target {z_low0:.3f}), zmax {bbA.zmax:.3f} (target {z_up1:.3f})"
        )
    except Exception as e:
        print(f"WARN: could not compute added = out.cut(base): {e}")

    return out