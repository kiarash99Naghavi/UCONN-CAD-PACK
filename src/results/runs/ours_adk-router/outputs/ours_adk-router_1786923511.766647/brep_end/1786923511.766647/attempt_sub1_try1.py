def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 22:
        print("ERROR: expected at least 22 solids (original 21 + added stand copy).")
        return shape

    # --- Identify the two stand solids to trim ---
    # Original stand body is s17 (0-based index 17) per prior accepted state.
    idx_orig = 17
    idx_new = len(sols) - 1  # the translated congruent copy was appended as a separate body

    s_orig = sols[idx_orig]
    s_new = sols[idx_new]

    bb_orig = s_orig.BoundingBox()
    bb_new = s_new.BoundingBox()

    print(
        "SELECTED: 1 solid for original stand s17  "
        f"idx={idx_orig} bboxY=[{bb_orig.ymin:.3f},{bb_orig.ymax:.3f}] bboxZ=[{bb_orig.zmin:.3f},{bb_orig.zmax:.3f}]"
    )
    print(
        "SELECTED: 1 solid for new stand copy  "
        f"idx={idx_new} bboxY=[{bb_new.ymin:.3f},{bb_new.ymax:.3f}] (target ~50.8..76.2)  "
        f"bboxZ=[{bb_new.zmin:.3f},{bb_new.zmax:.3f}]"
    )

    # Sanity check on the Y spans
    print(
        "CHECK: named Y span for new stand is 50.8..76.2; "
        f"dYmin={bb_new.ymin-50.8:.3f} dYmax={bb_new.ymax-76.2:.3f}"
    )

    # --- Build the horizontal half-space cut as a big box BELOW Z=-115.0 ---
    z_cut = -115.0
    z_low = -1000.0
    Lx = 1000.0
    Ly = 1000.0
    Lz = z_cut - z_low  # box top at z_cut

    cutter = cq.Solid.makeBox(Lx, Ly, Lz, cq.Vector(-Lx / 2.0, -Ly / 2.0, z_low))
    bb_cutter = cutter.BoundingBox()
    print(
        "TOOL: horizontal cutter box (removes material below Z=-115.0) "
        f"bboxZ=[{bb_cutter.zmin:.3f},{bb_cutter.zmax:.3f}] (expect [-1000.0,-115.0])"
    )

    # --- Cut EACH stand body separately (do NOT touch s0 or others) ---
    try:
        s_orig_trim = s_orig.cut(cutter)
        print("OK: cut original stand s17")
    except Exception as e:
        print(f"ERROR: failed cutting original stand s17: {e}")
        return shape

    try:
        s_new_trim = s_new.cut(cutter)
        print("OK: cut new stand copy")
    except Exception as e:
        print(f"ERROR: failed cutting new stand copy: {e}")
        return shape

    # --- Verification: no stand material below Z=-115, and each has a planar support at Z=-115 ---
    def report_support(solid, name):
        bb = solid.BoundingBox()
        print(
            f"VERIFY: {name} bboxZmin={bb.zmin:.6f} (target {z_cut:.3f}) dZmin={bb.zmin - z_cut:.6f}  "
            f"bboxZ=[{bb.zmin:.3f},{bb.zmax:.3f}]"
        )

        # Find horizontal planar faces near Z=-115
        horiz = []
        for i, f in enumerate(solid.Faces()):
            try:
                if hasattr(f, "geomType") and f.geomType() != "PLANE":
                    continue
                n = f.normalAt()
                c = f.Center()
                if abs(n.z) > 0.98 and abs(c.z - z_cut) < 0.5:
                    horiz.append((i, f, c, n, f.Area()))
            except Exception:
                continue

        print(f"SELECTED: {len(horiz)} planar horizontal faces near Z={z_cut:.3f} on {name}")
        if horiz:
            # Sort by area descending for readability
            horiz.sort(key=lambda t: t[4], reverse=True)
            for (i, _f, c, n, a) in horiz[:6]:
                print(
                    f"  FACE: idx={i} area={a:.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) "
                    f"normal=({n.x:.3f},{n.y:.3f},{n.z:.3f})"
                )

        return bb.zmin

    zmin_orig = report_support(s_orig_trim, "original stand s17 (trimmed)")
    zmin_new = report_support(s_new_trim, "new stand copy (trimmed)")
    print(f"COPLANAR CHECK: zmin delta (new - orig) = {zmin_new - zmin_orig:.6f} (target 0.0)")

    # --- Recompound with ONLY these two solids replaced ---
    out_sols = list(sols)
    out_sols[idx_orig] = s_orig_trim
    out_sols[idx_new] = s_new_trim

    out = cq.Compound.makeCompound(out_sols)

    # Extra isolation check: confirm no other solids were modified (by bbox equality prints)
    # (We do not have pre-edit cached bboxes for all, but we can at least state which indices changed.)
    print(f"INFO: modified solids indices = [{idx_orig}, {idx_new}] ; all others passed through untouched")

    return out