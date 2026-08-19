def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    # --- Resolve and print the referenced entities (global indices) ---
    faces = base.Faces()
    edges = base.Edges()
    print(f"INFO: base faces={len(faces)} edges={len(edges)} solids={len(base.Solids())}")

    try:
        f30 = faces[30]
        c30 = f30.Center()
        print(f"RESOLVED: face #30 center={tuple(round(v, 3) for v in (c30.x, c30.y, c30.z))}  (expected ~[42.743,1.753,0.504])")
    except Exception as e:
        print(f"ERROR: could not resolve face #30: {e}")
        f30 = None

    for ei in [65, 71]:
        try:
            e = edges[ei]
            c = e.Center()
            print(f"RESOLVED: edge #{ei} center={tuple(round(v, 3) for v in (c.x, c.y, c.z))}  (expected on pin end circle)")
        except Exception as e:
            print(f"ERROR: could not resolve edge #{ei}: {e}")

    # --- Edit only solid s1 (solid index 1 in the provided listing) ---
    solids = base.Solids()
    if len(solids) < 2:
        print("SELECTED: 0 solids for s1 (need at least 2 solids) -- NO-OP")
        return shape

    s1 = solids[1]
    bb1 = s1.BoundingBox()
    print(
        "SELECTED: 1 solid for s1 (target long pin) "
        f"bbox=([{bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}]..[{bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f}])"
    )

    # --- Parameters from sub-goal ---
    x0, y0 = 42.743, 1.753
    head_r = 4.0  # 8.0mm diameter
    head_h = 1.2
    # Required z extents:
    z_low0, z_low1 = -27.496, -26.296
    z_up0, z_up1 = 27.304, 28.504

    print("TARGET NUMBERS:")
    print(f"  center XY = ({x0:.3f}, {y0:.3f})")
    print(f"  lower head Z = {z_low0:.3f}..{z_low1:.3f}  (thk {head_h})")
    print(f"  upper head Z = {z_up0:.3f}..{z_up1:.3f}  (thk {head_h})")

    def build_head(z0, h):
        pl = cq.Plane(origin=(x0, y0, z0), normal=(0, 0, 1))
        print(f"PLANE: origin=({x0:.3f},{y0:.3f},{z0:.3f}) normal=(0,0,1)")
        return cq.Workplane(pl).circle(head_r).extrude(h).val()

    head_low = build_head(z_low0, head_h)
    head_up = build_head(z_up0, head_h)

    # Print achieved head centers and Z extents
    for name, hd, zt0, zt1 in [
        ("lower", head_low, z_low0, z_low1),
        ("upper", head_up, z_up0, z_up1),
    ]:
        c = hd.Center()
        bb = hd.BoundingBox()
        print(
            f"BUILT: {name} head center=({c.x:.3f},{c.y:.3f},{c.z:.3f})  "
            f"bboxZ={bb.zmin:.3f}..{bb.zmax:.3f}  "
            f"dXY=({c.x-x0:+.3f},{c.y-y0:+.3f})  "
            f"dZext=({bb.zmin-zt0:+.3f},{bb.zmax-zt1:+.3f})"
        )

    # If displaced (shouldn't be), correct via translation in same attempt
    def corrected(hd, target_x, target_y, target_zmin, target_zmax):
        bb = hd.BoundingBox()
        c = hd.Center()
        dx, dy = target_x - c.x, target_y - c.y
        dz = target_zmin - bb.zmin
        # This simultaneously aligns zmin; if height is correct, zmax follows.
        if abs(dx) > 1e-3 or abs(dy) > 1e-3 or abs(dz) > 1e-3 or abs(bb.zmax - target_zmax) > 1e-3:
            print(
                f"CORRECTING: translate ({dx:+.3f},{dy:+.3f},{dz:+.3f}) "
                f"to hit XY=({target_x:.3f},{target_y:.3f}) and Z={target_zmin:.3f}..{target_zmax:.3f}"
            )
            hd2 = hd.translate((dx, dy, dz))
            bb2 = hd2.BoundingBox()
            c2 = hd2.Center()
            print(
                f"CORRECTED: center=({c2.x:.3f},{c2.y:.3f},{c2.z:.3f}) bboxZ={bb2.zmin:.3f}..{bb2.zmax:.3f}"
            )
            return hd2
        return hd

    head_low = corrected(head_low, x0, y0, z_low0, z_low1)
    head_up = corrected(head_up, x0, y0, z_up0, z_up1)

    # Fuse heads onto s1 only
    edited_s1 = s1.fuse(head_low).fuse(head_up)
    print("BOOLEAN: fused 2 heads onto s1")

    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != 1] + [edited_s1])

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
            "CHECK: added Z mins/maxs vs targets: "
            f"zmin {bbA.zmin:.3f} (target {z_low0:.3f}), zmax {bbA.zmax:.3f} (target {z_up1:.3f})"
        )
    except Exception as e:
        print(f"WARN: could not compute added = out.cut(base): {e}")

    return out