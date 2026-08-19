def my_cad_function(args):
    import cadquery as cq

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"INFO: imported solids={len(solids)}")
    if len(solids) < 1:
        print("ERROR: no solids found")
        return shape

    bb_before = base.BoundingBox()
    print(f"INFO: bbox BEFORE min={[bb_before.xmin, bb_before.ymin, bb_before.zmin]} max={[bb_before.xmax, bb_before.ymax, bb_before.zmax]}")

    # --- Resolve the referenced faces by index (per requirement) and print them ---
    faces_all = base.Faces()
    print(f"INFO: total faces={len(faces_all)}")
    for idx in [1, 6]:
        try:
            f = faces_all[idx]
            c = f.Center()
            n = f.normalAt()
            print(
                f"SELECTED: 1 face idx={idx} for referenced stabilizing-rib side identification "
                f"geomType={f.geomType()} center={[round(c.x,3), round(c.y,3), round(c.z,3)]} "
                f"normal={[round(n.x,3), round(n.y,3), round(n.z,3)]} area={round(f.Area(),3)}"
            )
        except Exception as e:
            print(f"SELECTED: 0 faces idx={idx} (FAILED) for referenced stabilizing-rib side identification err={e}")

    # Also: find coplanar candidate side faces at z≈2 (-Z normal) and z≈6 (+Z normal)
    planar = [f for f in faces_all if f.geomType() == "PLANE"]
    z2 = []
    z6 = []
    for i, f in enumerate(planar):
        try:
            c = f.Center()
            n = f.normalAt()
            if abs(n.z + 1.0) < 1e-2 and abs(c.z - 2.0) < 0.2:
                z2.append(f)
            if abs(n.z - 1.0) < 1e-2 and abs(c.z - 6.0) < 0.2:
                z6.append(f)
        except Exception:
            pass
    print(f"SELECTED: {len(z2)} planar faces for z≈2, normal≈-Z (attachment side)")
    print(f"SELECTED: {len(z6)} planar faces for z≈6, normal≈+Z (attachment side)")

    # --- Build ribs as triangular prisms extruded along +X ---
    # Targets named by sub-goal
    x0, x1 = -7.0, 80.0
    y0, y1 = 11.0, 15.0
    zmin_neg, zattach_neg = -3.0, 2.0
    zattach_pos, zmax_pos = 6.0, 11.0
    L = x1 - x0

    print(
        "INFO: rib targets: "
        f"x[{x0},{x1}] y[{y0},{y1}] "
        f"-Z rib z[{zmin_neg},{zattach_neg}] (+Z-facing attach at z={zattach_neg}) "
        f"+Z rib z[{zattach_pos},{zmax_pos}] (-Z-facing attach at z={zattach_pos})"
    )

    # Workplane is YZ at x=x0, with +X extrusion direction
    rib_plane = cq.Plane(origin=(x0, 0, 0), normal=(1, 0, 0), xDir=(0, 1, 0))
    print(f"INFO: rib sketch plane origin={[x0,0,0]} normal={[1,0,0]} xDir={[0,1,0]} (local x=world Y, local y=world Z)")

    def make_rib(tri_pts_yz, name):
        wp = cq.Workplane(rib_plane)
        rib = wp.polyline(tri_pts_yz).close().extrude(L).val()
        bb = rib.BoundingBox()
        print(
            f"INFO: {name} rib SOLID bbox min={[round(bb.xmin,3), round(bb.ymin,3), round(bb.zmin,3)]} "
            f"max={[round(bb.xmax,3), round(bb.ymax,3), round(bb.zmax,3)]}"
        )
        return rib

    # Triangular YZ sections (in local coords: (Y, Z))
    tri_neg = [(y0, zattach_neg), (y1, zattach_neg), (y0, zmin_neg)]
    tri_pos = [(y0, zattach_pos), (y1, zattach_pos), (y0, zmax_pos)]

    rib_neg = make_rib(tri_neg, "-Z")
    rib_pos = make_rib(tri_pos, "+Z")

    # Self-check bbox vs targets, and correct by rebuild+translation if needed
    def check_and_correct(rib, target, name):
        # target: dict with xmin,xmax,ymin,ymax,zmin,zmax
        bb = rib.BoundingBox()
        got = {
            "xmin": bb.xmin,
            "xmax": bb.xmax,
            "ymin": bb.ymin,
            "ymax": bb.ymax,
            "zmin": bb.zmin,
            "zmax": bb.zmax,
        }
        deltas = {k: got[k] - target[k] for k in got}
        print(
            f"CHECK: {name} rib extrema got="
            f"x[{got['xmin']:.3f},{got['xmax']:.3f}] y[{got['ymin']:.3f},{got['ymax']:.3f}] z[{got['zmin']:.3f},{got['zmax']:.3f}]"
        )
        print(
            f"CHECK: {name} rib deltas vs target "
            f"dxmin={deltas['xmin']:.3f} dxmax={deltas['xmax']:.3f} "
            f"dymin={deltas['ymin']:.3f} dymax={deltas['ymax']:.3f} "
            f"dzmin={deltas['zmin']:.3f} dzmax={deltas['zmax']:.3f}"
        )

        # If off by more than ~0.2mm on any extreme, translate to correct.
        tol = 0.2
        if any(abs(deltas[k]) > tol for k in deltas):
            tx = target["xmin"] - got["xmin"]
            ty = target["ymin"] - got["ymin"]
            tz = target["zmin"] - got["zmin"]
            print(f"WARNING: {name} rib bbox off >{tol}mm; translating by {[round(tx,3), round(ty,3), round(tz,3)]} and re-checking")
            rib2 = rib.translate((tx, ty, tz))
            bb2 = rib2.BoundingBox()
            print(
                f"INFO: {name} rib AFTER translate bbox min={[round(bb2.xmin,3), round(bb2.ymin,3), round(bb2.zmin,3)]} "
                f"max={[round(bb2.xmax,3), round(bb2.ymax,3), round(bb2.zmax,3)]}"
            )
            return rib2
        return rib

    target_neg = {"xmin": x0, "xmax": x1, "ymin": y0, "ymax": y1, "zmin": zmin_neg, "zmax": zattach_neg}
    target_pos = {"xmin": x0, "xmax": x1, "ymin": y0, "ymax": y1, "zmin": zattach_pos, "zmax": zmax_pos}

    rib_neg = check_and_correct(rib_neg, target_neg, "-Z")
    rib_pos = check_and_correct(rib_pos, target_pos, "+Z")

    # --- Fuse ribs onto the sole solid (solid[0]) ---
    src_solid = solids[0]
    print("SELECTED: 1 solid[0] for rib union")

    # First attempt: fuse as-is
    try:
        edited = src_solid.fuse(rib_neg)
        edited = edited.fuse(rib_pos)
        out_sols = edited.Solids()
        print(f"INFO: after fuse attempt #1, solids in result={len(out_sols)}")
    except Exception as e:
        print(f"ERROR: fuse attempt #1 failed: {e}")
        edited = src_solid

    # If fuse produced more than one solid (likely only touching), rebuild ribs with slight overlap into rail to guarantee intersection
    if len(edited.Solids()) != 1:
        eps = 0.2
        print(f"WARNING: fuse did not yield a single solid; rebuilding ribs with internal overlap eps={eps}mm into rail")
        tri_neg_ol = [(y0, zattach_neg + eps), (y1, zattach_neg + eps), (y0, zmin_neg)]
        tri_pos_ol = [(y0, zattach_pos - eps), (y1, zattach_pos - eps), (y0, zmax_pos)]
        rib_neg_ol = make_rib(tri_neg_ol, "-Z (overlap)")
        rib_pos_ol = make_rib(tri_pos_ol, "+Z (overlap)")

        # We still want the *effective* added material to respect the stated ranges.
        # Fuse with overlap ribs (overlap is inside existing rail so it should not change the outer envelope).
        edited = src_solid.fuse(rib_neg_ol).fuse(rib_pos_ol)
        print(f"INFO: after fuse attempt #2, solids in result={len(edited.Solids())}")

    # Re-compound other solids untouched (if any)
    out = edited
    if len(solids) > 1:
        out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != 0] + [edited])

    bb_after = out.BoundingBox()
    print(f"INFO: bbox AFTER  min={[bb_after.xmin, bb_after.ymin, bb_after.zmin]} max={[bb_after.xmax, bb_after.ymax, bb_after.zmax]}")

    # --- Placement self-check: isolate newly added material and print its extrema ---
    # (This captures the actual net added ribs, even if we used overlap for fuse.)
    try:
        added = edited.cut(src_solid)
        bb_added = added.BoundingBox()
        vol_added = None
        try:
            vol_added = added.Volume()
        except Exception:
            pass
        print(
            f"INFO: net ADDED material (edited - base) volume={None if vol_added is None else round(vol_added,3)} "
            f"bbox min={[round(bb_added.xmin,3), round(bb_added.ymin,3), round(bb_added.zmin,3)]} "
            f"max={[round(bb_added.xmax,3), round(bb_added.ymax,3), round(bb_added.zmax,3)]}"
        )
        print(
            "INFO: expected added ribs to reach x[-7,80], y[11,15], and z to cover both sides within overall envelope z[-3,11]. "
            "(Per-side rib z ranges requested: -Z rib z[-3,2], +Z rib z[6,11])"
        )
    except Exception as e:
        print(f"WARNING: could not compute net added material (cut failed): {e}")

    return out