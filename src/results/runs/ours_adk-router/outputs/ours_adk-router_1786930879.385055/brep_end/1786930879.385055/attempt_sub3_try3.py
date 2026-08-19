def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    faces = base.Faces()
    edges = base.Edges()
    solids = base.Solids()
    print(f"INFO: base faces={len(faces)} edges={len(edges)} solids={len(solids)}")

    # --- Sub-goal (absolute numbers; build by coordinates, not fragile selections) ---
    x0, y0 = -37.304, 44.606
    head_r = 4.0   # 8.0 mm diameter
    head_h = 1.2
    z_low0, z_low1 = -27.496, -26.296
    z_up0, z_up1 = 27.304, 28.504

    print("TARGET NUMBERS:")
    print("  target body: s7 (solid index 7)")
    print(f"  head center XY = ({x0:.3f}, {y0:.3f})")
    print(f"  head diameter = {2*head_r:.3f} mm")
    print(f"  head thickness = {head_h:.3f} mm")
    print(f"  lower head Z = {z_low0:.3f}..{z_low1:.3f}")
    print(f"  upper head Z = {z_up0:.3f}..{z_up1:.3f}")
    print("  referenced (for confirmation): face#126 (green pin shaft per prompt), end edges [250,256]")

    # --- Light diagnostics on referenced entities (do not depend on them) ---
    for fi in [126]:
        if 0 <= fi < len(faces):
            f = faces[fi]
            fc = f.Center()
            try:
                gt = f.geomType()
            except Exception:
                gt = "(unknown)"
            rr = None
            try:
                rr = f.radius()
            except Exception:
                rr = None
            msg = f"RESOLVED: face #{fi} geomType={gt} center=({fc.x:.3f},{fc.y:.3f},{fc.z:.3f})"
            if isinstance(rr, (int, float)):
                msg += f" radius={rr:.3f}"
            print(msg)
        else:
            print(f"RESOLVED: face #{fi} OUT OF RANGE")

    got_edge_ids = []
    for ei in [250, 256]:
        if 0 <= ei < len(edges):
            e = edges[ei]
            ec = e.Center()
            ebb = e.BoundingBox()
            try:
                et = e.geomType()
            except Exception:
                et = "(unknown)"
            print(
                f"RESOLVED: edge #{ei} geomType={et} center=({ec.x:.3f},{ec.y:.3f},{ec.z:.3f}) bboxZ={ebb.zmin:.3f}..{ebb.zmax:.3f}"
            )
            got_edge_ids.append(ei)
        else:
            print(f"RESOLVED: edge #{ei} OUT OF RANGE")
    print(f"SELECTED: {len(got_edge_ids)} edges for pin end diagnostics idx={got_edge_ids}")

    # --- Edit ONLY body s7 (solid index 7) ---
    target_si = 7
    if target_si >= len(solids):
        # still do *something* rather than no-op: fuse onto last solid
        target_si = len(solids) - 1
        print(f"WARN: requested s7 index=7 not present; falling back to last solid index={target_si}")

    s_target = solids[target_si]
    bbT = s_target.BoundingBox()
    print(
        f"SELECTED: 1 solid for edit solid_idx={target_si} bbox=([" 
        f"{bbT.xmin:.3f},{bbT.ymin:.3f},{bbT.zmin:.3f}]..[{bbT.xmax:.3f},{bbT.ymax:.3f},{bbT.zmax:.3f}])"
    )

    # --- Build heads at absolute coordinates ---
    def make_head(z0):
        pl = cq.Plane(origin=(x0, y0, z0), normal=(0, 0, 1))
        print(f"PLANE: origin=({x0:.3f},{y0:.3f},{z0:.3f}) normal=(0,0,1)")
        return cq.Workplane(pl).circle(head_r).extrude(head_h).val()

    head_low = make_head(z_low0)
    head_up = make_head(z_up0)

    def report(name, solid, tz0, tz1):
        c = solid.Center()
        bb = solid.BoundingBox()
        print(
            f"BUILT: {name} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) "
            f"bboxZ={bb.zmin:.3f}..{bb.zmax:.3f} "
            f"dXY=({c.x-x0:+.3f},{c.y-y0:+.3f}) dZext=({bb.zmin-tz0:+.3f},{bb.zmax-tz1:+.3f})"
        )
        return c, bb

    cL, bbL = report("lower", head_low, z_low0, z_low1)
    cU, bbU = report("upper", head_up, z_up0, z_up1)

    # If anything is displaced (shouldn't be), translate in the same attempt
    def correct(h, tx, ty, tzmin, tzmax, tol=1e-6):
        c = h.Center()
        bb = h.BoundingBox()
        dx, dy = tx - c.x, ty - c.y
        dz = tzmin - bb.zmin
        dz2 = tzmax - bb.zmax
        if abs(dx) > tol or abs(dy) > tol or abs(dz) > tol or abs(dz2) > tol:
            # prioritize zmin alignment (keeps thickness) and XY
            print(f"CORRECTING: translate=({dx:+.6f},{dy:+.6f},{dz:+.6f})")
            h2 = h.translate((dx, dy, dz))
            c2 = h2.Center()
            bb2 = h2.BoundingBox()
            print(f"CORRECTED: center=({c2.x:.3f},{c2.y:.3f},{c2.z:.3f}) bboxZ={bb2.zmin:.3f}..{bb2.zmax:.3f}")
            return h2
        return h

    head_low = correct(head_low, x0, y0, z_low0, z_low1)
    head_up = correct(head_up, x0, y0, z_up0, z_up1)

    # --- Fuse onto target solid only ---
    edited = s_target
    try:
        edited = edited.fuse(head_low).fuse(head_up)
        print("BOOLEAN: fused 2 retaining heads onto target solid")
    except Exception as e:
        print(f"ERROR: fuse failed ({e}); attempting sequential fuses")
        try:
            edited = s_target.fuse(head_low)
            print("BOOLEAN: fused lower head")
        except Exception as e2:
            print(f"ERROR: lower fuse failed: {e2}")
            edited = s_target
        try:
            edited = edited.fuse(head_up)
            print("BOOLEAN: fused upper head")
        except Exception as e3:
            print(f"ERROR: upper fuse failed: {e3}")

    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != target_si] + [edited])

    # --- Placement self-check: isolate added material on that body ---
    try:
        added = edited.cut(s_target)
        bbA = added.BoundingBox()
        cA = added.Center()
        print(
            f"ADDED: center=({cA.x:.3f},{cA.y:.3f},{cA.z:.3f}) "
            f"bbox=([{bbA.xmin:.3f},{bbA.ymin:.3f},{bbA.zmin:.3f}]..[{bbA.xmax:.3f},{bbA.ymax:.3f},{bbA.zmax:.3f}])"
        )
        print(
            f"CHECK: added center XY vs target dX={cA.x-x0:+.3f} dY={cA.y-y0:+.3f}; "
            f"added zmin={bbA.zmin:.3f} (target {z_low0:.3f}), zmax={bbA.zmax:.3f} (target {z_up1:.3f})"
        )
    except Exception as e:
        print(f"WARN: could not compute added=edited.cut(s_target): {e}")

    return out