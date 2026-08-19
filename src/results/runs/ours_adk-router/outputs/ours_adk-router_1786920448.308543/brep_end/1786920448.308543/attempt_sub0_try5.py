def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"SELECTED: {len(solids)} solids in STEP")
    if len(solids) == 0:
        print("SELECTED: 0 solids -> no-op")
        return shape

    # Pick s0 robustly as the largest-volume solid
    vols = [s.Volume() for s in solids]
    k0 = max(range(len(solids)), key=lambda i: vols[i])
    s0 = solids[k0]
    vol0 = s0.Volume()
    print(f"SELECTED: s0 as largest solid index={k0} volume={vol0:.3f} mm^3")

    # Diagnostic resolve planar face #24 (do not re-anchor from it)
    faces_all = base.Faces()
    if len(faces_all) > 24:
        f24 = faces_all[24]
        n24 = None
        try:
            n24 = f24.normalAt()
        except Exception as e:
            print(f"WARNING: face #24 normalAt() failed: {e}")
        print(
            "RESOLVED: face #24 center={} normal={} area={:.3f}".format(
                tuple(round(v, 3) for v in f24.Center().toTuple()),
                tuple(round(v, 6) for v in n24.toTuple()) if n24 else None,
                f24.Area(),
            )
        )
    else:
        print(f"SELECTED: 0 faces for face #24 check (faces={len(faces_all)})")

    # --- Targets (absolute) ---
    cx, cy, z_base = (-88.9, 100.0, 266.7)
    axis = (0.0, 0.0, 1.0)
    od = 38.1
    id_ = 25.4
    ro = od / 2.0
    ri = id_ / 2.0
    z_top = 296.7
    height = z_top - z_base

    print("TARGETS:")
    print(f"  neck center = ({cx}, {cy}, {z_base})")
    print(f"  axis = {axis}")
    print(f"  outside diameter = {od} (r={ro})")
    print(f"  inside  diameter = {id_} (r={ri})")
    print(f"  base z = {z_base}  end z = {z_top}  height = {height}")

    # Build annular neck directly as (outer cylinder - inner cylinder)
    outer = cq.Solid.makeCylinder(ro, height, pnt=cq.Vector(cx, cy, z_base), dir=cq.Vector(*axis))
    inner = cq.Solid.makeCylinder(ri, height, pnt=cq.Vector(cx, cy, z_base), dir=cq.Vector(*axis))
    neck = outer.cut(inner)

    nb = neck.BoundingBox()
    nbc = nb.center
    print(
        "NECK BUILT: bbox xmin/xmax=({:.4f},{:.4f}) ymin/ymax=({:.4f},{:.4f}) zmin/zmax=({:.4f},{:.4f}) center=({:.4f},{:.4f},{:.4f})".format(
            nb.xmin, nb.xmax, nb.ymin, nb.ymax, nb.zmin, nb.zmax, nbc.x, nbc.y, nbc.z
        )
    )

    # Self-check vs required extents
    exp_xmin, exp_xmax = (cx - ro, cx + ro)
    exp_ymin, exp_ymax = (cy - ro, cy + ro)
    print(
        "CHECK neck extents vs expected: x[{:.4f},{:.4f}] y[{:.4f},{:.4f}] z[{:.4f},{:.4f}]".format(
            exp_xmin, exp_xmax, exp_ymin, exp_ymax, z_base, z_top
        )
    )
    print(
        "DELTAS: dxmin={:.6f} dxmax={:.6f} dymin={:.6f} dymax={:.6f} dzmin={:.6f} dzmax={:.6f}".format(
            nb.xmin - exp_xmin,
            nb.xmax - exp_xmax,
            nb.ymin - exp_ymin,
            nb.ymax - exp_ymax,
            nb.zmin - z_base,
            nb.zmax - z_top,
        )
    )

    # Fuse strategy: prefer glue-mode fuse to avoid coplanar-face boolean artifacts
    def fuse_attempt(label, use_glue):
        try:
            if use_glue:
                try:
                    out = s0.fuse(neck, glue=True)
                except TypeError:
                    print(f"FUSE {label}: glue kw not supported -> falling back")
                    out = s0.fuse(neck)
            else:
                out = s0.fuse(neck)
            return out, None
        except Exception as e:
            return None, e

    edited_s0 = None
    for label, use_glue in [("A(glue)", True), ("B(no_glue)", False)]:
        cand, err = fuse_attempt(label, use_glue)
        if err:
            print(f"FUSE {label}: FAILED with {err}")
            continue

        # Ensure we ended with a single solid, not a multi-solid compound
        cand_solids = cand.Solids()
        print(f"FUSE {label}: produced {len(cand_solids)} solid(s)")
        if len(cand_solids) != 1:
            print(f"FUSE {label}: not a single fused solid -> rejecting this attempt")
            continue

        # Compute added material and verify it is confined to the annular neck region
        try:
            added = cand.cut(s0)
            added_vol = added.Volume()
            ab = added.BoundingBox()
            ac = added.Center()
            print(
                "ADDED {lbl} (cand - s0): vol={v:.3f} bbox x[{xmin:.4f},{xmax:.4f}] y[{ymin:.4f},{ymax:.4f}] z[{zmin:.4f},{zmax:.4f}] center={ctr}".format(
                    lbl=label,
                    v=added_vol,
                    xmin=ab.xmin,
                    xmax=ab.xmax,
                    ymin=ab.ymin,
                    ymax=ab.ymax,
                    zmin=ab.zmin,
                    zmax=ab.zmax,
                    ctr=tuple(round(t, 3) for t in ac.toTuple()),
                )
            )

            # Confinement check: y-span must be approximately the OD (with small numerical slack)
            ylen = ab.ymax - ab.ymin
            xlen = ab.xmax - ab.xmin
            zlen = ab.zmax - ab.zmin
            print(f"ADDED {label}: spans xlen={xlen:.4f} ylen={ylen:.4f} zlen={zlen:.4f}")

            # Heuristic gate: added volume must be positive and added bbox must not exceed OD envelope meaningfully
            if added_vol <= 0.0:
                print(f"FUSE {label}: added_vol <= 0 -> rejecting")
                continue
            if ylen > (od + 0.5) or xlen > (od + 0.5) or abs(zlen - height) > 0.5:
                print(
                    f"FUSE {label}: added bbox not confined to expected neck (xlen/ylen too large or zlen wrong) -> rejecting"
                )
                continue

        except Exception as e:
            print(f"FUSE {label}: WARNING could not compute/validate added region: {e} -> rejecting")
            continue

        edited_s0 = cand_solids[0]
        print(f"FUSE {label}: ACCEPTED")
        break

    if edited_s0 is None:
        print("ERROR: could not produce a single fused solid with confined added neck geometry -> returning input unchanged")
        return shape

    # Final reporting
    expected_vol = 3.141592653589793 * (ro * ro - ri * ri) * height
    print(f"EXPECTED neck volume (analytic) ~= {expected_vol:.3f} mm^3")
    print("FINAL NECK REPORT (requested):")
    print(f"  neck center = ({cx}, {cy}, {z_base})")
    print(f"  measured axis = {axis}")
    print(f"  OD={od} ID={id_}")
    print(f"  base z={z_base} end z={z_top}")

    # Recompound: keep all other bodies untouched
    out = cq.Compound.makeCompound([s for i, s in enumerate(solids) if i != k0] + [edited_s0])
    return out