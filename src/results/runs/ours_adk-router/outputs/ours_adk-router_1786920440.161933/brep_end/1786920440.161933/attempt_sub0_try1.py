def my_cad_function(args):
    import cadquery as cq
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    solid = sols[0] if len(sols) else base

    faces = solid.Faces()
    print(f"INFO: solid faces={len(faces)} edges={len(solid.Edges())} verts={len(solid.Vertices())}")

    # --- Resolve referenced faces by index and verify against provided geometry index ---
    f77 = faces[77]
    f99 = faces[99]
    print("SELECTED: 1 face for floor reference idx=[77]")
    print("SELECTED: 1 face for underside bridge reference idx=[99]")

    def _face_report(f, idx):
        c = f.Center()
        n = f.normalAt()
        a = f.Area()
        bb = f.BoundingBox()
        print(
            f"FACE#{idx}: area={a:.3f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) "
            f"normal=({n.x:.3f},{n.y:.3f},{n.z:.3f}) "
            f"bbx=({bb.xmin:.3f}..{bb.xmax:.3f}) bby=({bb.ymin:.3f}..{bb.ymax:.3f}) bbz=({bb.zmin:.3f}..{bb.zmax:.3f})"
        )
        return c, n, bb

    c77, n77, bb77 = _face_report(f77, 77)
    c99, n99, bb99 = _face_report(f99, 99)

    # --- Rib targets (explicit numbers from the sub-goal) ---
    target_xc = 6.123
    target_xmin = 5.373
    target_xmax = 6.873
    target_dx = target_xmax - target_xmin  # 1.5
    floor_y = -0.01
    print(
        "TARGETS: "
        f"x_center={target_xc:.3f}  x_limits=({target_xmin:.3f}..{target_xmax:.3f})  dx={target_dx:.3f}  "
        f"floor_y={floor_y:.3f}"
    )

    part_bb = solid.BoundingBox()
    print(
        f"PART_BBOX(before): xmin={part_bb.xmin:.3f} xmax={part_bb.xmax:.3f} "
        f"ymin={part_bb.ymin:.3f} ymax={part_bb.ymax:.3f} zmin={part_bb.zmin:.3f} zmax={part_bb.zmax:.3f}"
    )

    # Choose a Z-span guided by face#99 extent, but clipped to avoid the known X-axis bore family at z≈0.88
    # (bore centers at z=0.88, r=0.24 => avoid roughly z>0.64).
    zmin = bb99.zmin - 0.2
    zmax = min(bb99.zmax + 0.2, 0.60)
    if (zmax - zmin) < 0.8:
        # If this would become too narrow (unlikely), relax the z-limit.
        zmax = bb99.zmax + 0.2
    print(f"RIB_Z_SPAN(initial): zmin={zmin:.3f} zmax={zmax:.3f} dz={(zmax - zmin):.3f}")

    def _build_rib(xmin, xmax):
        dx = xmax - xmin
        dy = part_bb.ylen + 50.0  # overshoot in +Y then clip by plane99 half-space
        dz = zmax - zmin
        rib_raw = cq.Solid.makeBox(dx, dy, dz, cq.Vector(xmin, floor_y, zmin))

        # Half-space prism: plane of face#99, extruded along +normal, so we KEEP points on +normal side.
        # Check sign at the floor point; we want floor_y side to be retained.
        plane99 = cq.Plane(
            origin=(c99.x, c99.y, c99.z),
            normal=(n99.x, n99.y, n99.z),
            xDir=(1, 0, 0),
        )
        print(
            f"PLANE99(constructed): origin=({c99.x:.3f},{c99.y:.3f},{c99.z:.3f}) "
            f"normal=({n99.x:.3f},{n99.y:.3f},{n99.z:.3f})"
        )

        # Signed distance of the intended floor point to plane99
        pb = cq.Vector((xmin + xmax) * 0.5, floor_y, (zmin + zmax) * 0.5)
        d_floor = n99.x * (pb.x - c99.x) + n99.y * (pb.y - c99.y) + n99.z * (pb.z - c99.z)
        print(f"CHECK: signed_dist(plane99) at rib floor probe point = {d_floor:.4f} (should be >=0 to be kept)")

        halfspace = cq.Workplane(plane99).rect(200, 200).extrude(200).val()
        rib_clip = rib_raw.intersect(halfspace)

        # Clip to a tight box around the original part bbox to guarantee no bbox growth
        clip = cq.Solid.makeBox(
            part_bb.xlen + 1e-3,
            part_bb.ylen + 1e-3,
            part_bb.zlen + 1e-3,
            cq.Vector(part_bb.xmin, part_bb.ymin, part_bb.zmin),
        )
        rib_final = rib_clip.intersect(clip)

        bb = rib_final.BoundingBox()
        print(
            f"RIB_BBOX(pre-fuse): xmin={bb.xmin:.3f} xmax={bb.xmax:.3f} (dx={bb.xlen:.3f}) "
            f"ymin={bb.ymin:.3f} ymax={bb.ymax:.3f} zmin={bb.zmin:.3f} zmax={bb.zmax:.3f}"
        )

        # Verify it meets plane99: compute min signed distance over rib vertices (should be ~0)
        vds = []
        for v in rib_final.Vertices():
            p = v.Center()
            d = n99.x * (p.x - c99.x) + n99.y * (p.y - c99.y) + n99.z * (p.z - c99.z)
            vds.append(d)
        if vds:
            print(f"RIB_vs_PLANE99: min_signed_dist={min(vds):.4f} max_signed_dist={max(vds):.4f} (expect min near 0)")
        else:
            print("RIB_vs_PLANE99: WARNING no vertices found on rib_final")

        return rib_final

    # First build
    rib = _build_rib(target_xmin, target_xmax)

    # Fuse
    out = solid.fuse(rib)
    out_bb = out.BoundingBox()
    print(
        f"PART_BBOX(after):  xmin={out_bb.xmin:.3f} xmax={out_bb.xmax:.3f} "
        f"ymin={out_bb.ymin:.3f} ymax={out_bb.ymax:.3f} zmin={out_bb.zmin:.3f} zmax={out_bb.zmax:.3f}"
    )
    print(
        "BBOX_DELTA: "
        f"dxmin={out_bb.xmin - part_bb.xmin:.4f} dxmax={out_bb.xmax - part_bb.xmax:.4f} "
        f"dymin={out_bb.ymin - part_bb.ymin:.4f} dymax={out_bb.ymax - part_bb.ymax:.4f} "
        f"dzmin={out_bb.zmin - part_bb.zmin:.4f} dzmax={out_bb.zmax - part_bb.zmax:.4f}"
    )

    # Isolate newly added material and report achieved X limits/center
    added = out.cut(solid)
    added_bb = added.BoundingBox()
    achieved_xmin = added_bb.xmin
    achieved_xmax = added_bb.xmax
    achieved_xc = 0.5 * (achieved_xmin + achieved_xmax)
    print(
        f"ADDED_BBOX: xmin={added_bb.xmin:.3f} xmax={added_bb.xmax:.3f} (dx={added_bb.xlen:.3f}) "
        f"ymin={added_bb.ymin:.3f} ymax={added_bb.ymax:.3f} zmin={added_bb.zmin:.3f} zmax={added_bb.zmax:.3f}"
    )
    print(
        f"ACHIEVED_RIB_X: limits=({achieved_xmin:.3f}..{achieved_xmax:.3f}) center={achieved_xc:.3f}  "
        f"(target limits=({target_xmin:.3f}..{target_xmax:.3f}) center={target_xc:.3f})"
    )
    print(
        "ACHIEVED_DELTAS_X: "
        f"dxmin={achieved_xmin - target_xmin:.4f} dxmax={achieved_xmax - target_xmax:.4f} dxc={achieved_xc - target_xc:.4f}"
    )

    # Correct in the same attempt if the achieved rib x-range is off
    tol = 0.02
    if (abs(achieved_xmin - target_xmin) > tol) or (abs(achieved_xmax - target_xmax) > tol):
        shift = target_xc - achieved_xc
        print(f"CORRECTION: shifting rib by dx={shift:.4f} and rebuilding")
        rib2 = _build_rib(target_xmin + shift, target_xmax + shift)
        out2 = solid.fuse(rib2)
        added2 = out2.cut(solid)
        bb2 = added2.BoundingBox()
        achieved2_xmin = bb2.xmin
        achieved2_xmax = bb2.xmax
        achieved2_xc = 0.5 * (achieved2_xmin + achieved2_xmax)
        print(
            f"ACHIEVED_RIB_X(after correction): limits=({achieved2_xmin:.3f}..{achieved2_xmax:.3f}) center={achieved2_xc:.3f} "
            f"dx={bb2.xlen:.3f}"
        )
        out = out2

    # Final sanity: ensure we ended with a single solid
    final_sols = out.Solids()
    print(f"INFO: final solids={len(final_sols)}")

    return out