def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in STEP")
    if len(solids) < 1:
        print("SELECTED: 0 solids -> no-op")
        return shape

    # Target body is s0 (largest, first in the index listing)
    s0 = solids[0]
    vol0 = s0.Volume()
    print(f"s0 original volume = {vol0:.3f} mm^3")

    # Resolve and print planar face #24 as requested (diagnostic only; do not re-anchor from it)
    faces_all = base.Faces()
    if len(faces_all) > 24:
        f24 = faces_all[24]
        try:
            n24 = f24.normalAt()
        except Exception as e:
            n24 = None
            print(f"WARNING: face #24 normalAt() failed: {e}")
        print(
            "RESOLVED: face #24  center={}  normal={}  area={:.3f}".format(
                tuple(round(v, 3) for v in f24.Center().toTuple()),
                tuple(round(v, 6) for v in n24.toTuple()) if n24 else None,
                f24.Area(),
            )
        )
    else:
        print(f"SELECTED: 0 faces for face #24 check (faces={len(faces_all)})")

    # --- Build the annular neck directly on the absolute measured plane ---
    # Given targets
    cx, cy, cz_base = (-88.9, 100.0, 266.7)
    axis = (0.0, 0.0, 1.0)
    od = 38.1
    id_ = 25.4
    ro = od / 2.0
    ri = id_ / 2.0
    z_top = 296.7
    height = z_top - cz_base

    print("TARGETS:")
    print(f"  neck center (base plane origin) = ({cx}, {cy}, {cz_base})")
    print(f"  axis = {axis}")
    print(f"  outside diameter = {od}  (r={ro})")
    print(f"  inside  diameter = {id_}  (r={ri})")
    print(f"  base z = {cz_base}  end z = {z_top}  height = {height}")

    def build_neck_at(origin_xyz):
        pl = cq.Plane(origin=origin_xyz, normal=axis)
        wp = cq.Workplane(pl)
        neck_wp = wp.circle(ro).circle(ri).extrude(height)
        return neck_wp.val()

    neck = build_neck_at((cx, cy, cz_base))

    # Placement self-check and correction if needed
    nb = neck.BoundingBox()
    # Use bbox center for a stable check on x/y and mid-z
    nbc = nb.center
    target_mid_z = (cz_base + z_top) / 2.0
    dx = cx - nbc.x
    dy = cy - nbc.y
    dz = cz_base - nb.zmin
    print(
        "NECK BUILT (pre-correct): bbox zmin={:.4f} zmax={:.4f} center=({:.4f},{:.4f},{:.4f})".format(
            nb.zmin, nb.zmax, nbc.x, nbc.y, nbc.z
        )
    )
    print(
        "DELTAS vs targets: dx={:.4f} dy={:.4f} dz_base={:.4f} dz_mid={:.4f}".format(
            dx, dy, dz, target_mid_z - nbc.z
        )
    )

    # If anything is off beyond tolerance, translate and recheck (same attempt)
    tol = 0.05  # mm
    if abs(dx) > tol or abs(dy) > tol or abs(dz) > tol:
        print("CORRECTION: translating neck to match requested absolute placement")
        neck = neck.translate(cq.Vector(dx, dy, dz))
        nb = neck.BoundingBox()
        nbc = nb.center
        print(
            "NECK BUILT (corrected): bbox zmin={:.4f} zmax={:.4f} center=({:.4f},{:.4f},{:.4f})".format(
                nb.zmin, nb.zmax, nbc.x, nbc.y, nbc.z
            )
        )

    # Fuse neck to s0 (purely additive; neck is already annular, leaving ID empty)
    edited_s0 = s0.fuse(neck)

    # Added material isolation and positive volume confirmation
    try:
        added = edited_s0.cut(s0)
        added_vol = added.Volume() if hasattr(added, "Volume") else 0.0
        ab = added.BoundingBox()
        print(
            "ADDED (edited_s0 - s0): volume={:.3f} mm^3  bbox zmin={:.4f} zmax={:.4f} center={}".format(
                added_vol,
                ab.zmin,
                ab.zmax,
                tuple(round(v, 3) for v in added.Center().toTuple()),
            )
        )
    except Exception as e:
        added = None
        added_vol = None
        print(f"WARNING: could not compute added volume via boolean difference: {e}")

    expected_vol = 3.141592653589793 * (ro * ro - ri * ri) * height
    print(f"EXPECTED neck volume (analytic) ~= {expected_vol:.3f} mm^3")

    print("FINAL NECK REPORT:")
    print(f"  neck center (requested) = ({cx}, {cy}, {cz_base})")
    print(f"  measured axis = {axis}")
    print(f"  OD={od}  ID={id_}")
    print(f"  base z={cz_base}  end z={z_top}")
    print(f"  neck bbox zmin={nb.zmin:.4f} zmax={nb.zmax:.4f}")
    if added_vol is not None:
        print(f"  CONFIRM positive added volume: {added_vol:.3f} mm^3")

    # Recompound: keep all other imported bodies untouched
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != 0] + [edited_s0])

    return out