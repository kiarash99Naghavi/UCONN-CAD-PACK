def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def vfmt(v):
        return f"({v.x:.3f},{v.y:.3f},{v.z:.3f})"

    def bbfmt(bb):
        return (
            f"min=({bb.xmin:.3f},{bb.ymin:.3f},{bb.zmin:.3f}) "
            f"max=({bb.xmax:.3f},{bb.ymax:.3f},{bb.zmax:.3f}) "
            f"len=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})"
        )

    def geom_type(face):
        try:
            return face.geomType()
        except Exception:
            return "UNKNOWN"

    def safe_clean(s):
        try:
            return s.clean()
        except Exception:
            return s

    # ---- Numbers named by the sub-goal / index ----
    bbox_exact = (0.0, 300.0, 200.0, 320.0, -445.0, -340.0)
    bore_r = 14.1421
    bore_yz = (270.0, -400.0)
    bore_xspan = (100.0, 300.0)

    print("NUMBERS:")
    print(f"  GLOBAL bbox X={bbox_exact[0]}..{bbox_exact[1]} Y={bbox_exact[2]}..{bbox_exact[3]} Z={bbox_exact[4]}..{bbox_exact[5]}")
    print(f"  PROTECT bore: r={bore_r} axis||X at Y={bore_yz[0]} Z={bore_yz[1]} spanning X={bore_xspan[0]}..{bore_xspan[1]}")
    print("  PROTECT cavities: via inner opening loops on planar faces near Y=230 and X=300")

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("SELECTED: 0 solids (ERROR)")
        return shape

    solid = sols[0]
    bb0 = solid.BoundingBox()
    vol0 = solid.Volume()
    print(f"INFO: base bbox {bbfmt(bb0)}")
    print(f"INFO: base volume={vol0:.3f} mm^3")

    faces = solid.Faces()
    print(f"INFO: solid faces={len(faces)}")

    # Global clip solid exactly matching measured bbox (prevents any tool from exceeding envelope)
    gx0, gx1, gy0, gy1, gz0, gz1 = bbox_exact
    gdx, gdy, gdz = (gx1 - gx0), (gy1 - gy0), (gz1 - gz0)
    gc = ((gx0 + gx1) * 0.5, (gy0 + gy1) * 0.5, (gz0 + gz1) * 0.5)
    global_clip = cq.Workplane(cq.Plane.XY()).box(gdx, gdy, gdz, centered=(True, True, True)).translate(gc).val()
    gbb = global_clip.BoundingBox()
    print(f"INFO: global_clip bbox {bbfmt(gbb)} (should match measured envelope)")

    # ---- Protected volumes ----
    # Protect the functional X-axis bore by explicit cylinder signature
    bore_pad = 0.35  # small safety so tools never touch bore wall
    bore_len = (bore_xspan[1] - bore_xspan[0]) + 20.0
    bore_x0 = bore_xspan[0] - 10.0
    bore_plane = cq.Plane(origin=(bore_x0, bore_yz[0], bore_yz[1]), normal=(1, 0, 0), xDir=(0, 1, 0))
    bore_protect = cq.Workplane(bore_plane).circle(bore_r + bore_pad).extrude(bore_len).val()
    bb_bore = bore_protect.BoundingBox()
    print(f"INFO: bore_protect bbox {bbfmt(bb_bore)}")

    # Protect cavities reachable through inner opening loops on planar faces #5 and #29
    cavity_protect = None
    cavity_face_indices = []
    for idx in [5, 29]:
        if idx < 0 or idx >= len(faces):
            continue
        f = faces[idx]
        if len(f.Wires()) > 1:
            cavity_face_indices.append(idx)

    print(f"SELECTED: {len(cavity_face_indices)} faces with inner opening loops for cavity protection   idx={cavity_face_indices}")

    for idx in cavity_face_indices:
        f = faces[idx]
        n = f.normalAt()  # outward
        inward = cq.Vector(-n.x, -n.y, -n.z)
        # pull the extrusion slightly outward first to ensure it fully covers the mouth
        shift = cq.Vector(n.x, n.y, n.z).multiply(0.2)
        wires = f.Wires()
        inner = wires[1:]
        print(f"INFO: cavity face #{idx} has inner_loops={len(inner)} center={vfmt(f.Center())} normal(out)={vfmt(n)}")
        for wi, w in enumerate(inner):
            try:
                cap = cq.Face.makeFromWires(w)
                tool = cq.Solid.extrudeLinear(cap, inward.multiply(600.0))
                tool = tool.translate(shift)
                cavity_protect = tool if cavity_protect is None else cavity_protect.fuse(tool)
            except Exception as e:
                print(f"WARN: cavity protect build failed on face #{idx} inner loop {wi}: {e}")

    if cavity_protect is not None:
        cavity_protect = safe_clean(cavity_protect)
        print(f"INFO: cavity_protect bbox {bbfmt(cavity_protect.BoundingBox())}")
    else:
        print("INFO: cavity_protect not built (no inner loops found)")

    protect = bore_protect
    if cavity_protect is not None:
        protect = safe_clean(protect.fuse(cavity_protect))

    # ---- Targets (by stable geometry-index face indices) ----
    # Exterior blend/chamfer targets per prompt
    target_face_indices = [
        6, 22, 24, 30, 32,   # CYL R30 partial sweeps
        4,                   # CYL R35 partial sweep
        10,                  # CYL R10 90deg
        7, 27,               # CYL R5 (one is small partial)
        12,                  # CYL R2.5
        0, 14,               # CONEs
        23,                  # SPHERE
        25, 20,              # BSPLINE corners
        1, 15, 11            # planar bevel strips with oblique normals
    ]

    # Approx radius for localization pad
    rmap = {
        6: 30.0, 22: 30.0, 24: 30.0, 30: 30.0, 32: 30.0,
        4: 35.0,
        10: 10.0,
        7: 5.0, 27: 5.0,
        12: 2.5,
        0: 10.0, 14: 10.0,
        23: 10.0,
        25: 8.0, 20: 8.0,
        1: 5.0, 15: 5.0, 11: 5.0,
    }

    resolved_targets = []
    for idx in target_face_indices:
        if idx < 0 or idx >= len(faces):
            print(f"SELECTED: 0 faces for target idx={idx} (out of range)")
            continue
        f = faces[idx]
        resolved_targets.append((idx, f))
        print(
            f"INFO: target face #{idx} type={geom_type(f)} area={f.Area():.3f} "
            f"center={vfmt(f.Center())}"
        )
    print(f"SELECTED: {len(resolved_targets)} target faces total for exterior de-blending")

    # ---- Adjacency / support plane collection ----
    def shared_edges(fa, fb):
        out = []
        ea = fa.Edges()
        eb = fb.Edges()
        for e1 in ea:
            for e2 in eb:
                try:
                    if e1.isSame(e2):
                        out.append(e1)
                        break
                except Exception:
                    pass
        return out

    def adjacent_face_indices(face_idx):
        fa = faces[face_idx]
        nbrs = set()
        for j, fb in enumerate(faces):
            if j == face_idx:
                continue
            if shared_edges(fa, fb):
                nbrs.add(j)
        return list(nbrs)

    def collect_support_planes(target_idx, needed):
        # BFS up to depth 2 through blend network to find planar supports
        visited = set([target_idx])
        frontier = [(target_idx, 0)]
        plane_scores = {}  # idx -> total shared edge length with visited blend cluster

        while frontier:
            cur, depth = frontier.pop(0)
            if depth > 2:
                continue
            fa = faces[cur]
            for j in adjacent_face_indices(cur):
                if j in visited:
                    continue
                fb = faces[j]
                # score by shared edge length with current
                se = shared_edges(fa, fb)
                sl = 0.0
                for e in se:
                    try:
                        sl += e.Length()
                    except Exception:
                        pass

                if geom_type(fb) == "PLANE":
                    plane_scores[j] = plane_scores.get(j, 0.0) + sl
                else:
                    # traverse through other blends to reach planes
                    if depth < 2:
                        frontier.append((j, depth + 1))

                visited.add(j)

        # pick top supports
        ranked = sorted(plane_scores.items(), key=lambda kv: kv[1], reverse=True)
        picked = [i for i, sc in ranked[:needed] if sc > 1e-6]
        picked_faces = [(i, faces[i], plane_scores[i]) for i in picked]
        return picked_faces

    # ---- Half-space wedge construction (explicit boolean corner patches) ----
    def make_local_clip(face_obj, pad):
        bb = face_obj.BoundingBox()
        cx = (bb.xmin + bb.xmax) * 0.5
        cy = (bb.ymin + bb.ymax) * 0.5
        cz = (bb.zmin + bb.zmax) * 0.5
        dx = bb.xlen + 2.0 * pad
        dy = bb.ylen + 2.0 * pad
        dz = bb.zlen + 2.0 * pad
        # Avoid degeneracy
        dx = max(dx, 1.0)
        dy = max(dy, 1.0)
        dz = max(dz, 1.0)
        return cq.Workplane(cq.Plane.XY()).box(dx, dy, dz, centered=(True, True, True)).translate((cx, cy, cz)).val()

    def halfspace_from_planar_face(pf):
        # Use the planar face itself as the boundary; choose the half-space containing a point slightly inside the solid
        n = pf.normalAt()  # outward
        inside_pt = pf.Center() - cq.Vector(n.x, n.y, n.z).multiply(1.0)
        try:
            hs = cq.Solid.makeHalfSpace(pf, inside_pt)
            return hs
        except Exception as e:
            print(f"ERROR: makeHalfSpace failed for plane face at {vfmt(pf.Center())}: {e}")
            return None

    def build_corner_patch(target_idx, target_face, support_plane_faces, pad):
        if len(support_plane_faces) < 2:
            print(f"SELECTED: 0 patches for face#{target_idx} (need >=2 planar supports)")
            return None

        local_clip = make_local_clip(target_face, pad)
        lbb = local_clip.BoundingBox()
        print(f"INFO: local_clip for face#{target_idx} pad={pad:.3f} bbox {bbfmt(lbb)}")

        wedge = None
        for (pi, pf, score) in support_plane_faces:
            hs = halfspace_from_planar_face(pf)
            if hs is None:
                continue
            wedge = hs if wedge is None else wedge.intersect(hs)

        if wedge is None:
            print(f"SELECTED: 0 wedges for face#{target_idx} (halfspace build failed)")
            return None

        try:
            wedge = wedge.intersect(local_clip)
            wedge = wedge.intersect(global_clip)
            # keep tools away from protected voids/bore/cavities
            wedge = wedge.cut(protect)
            wedge = safe_clean(wedge)
            return wedge
        except Exception as e:
            print(f"ERROR: wedge clip/cut failed for face#{target_idx}: {e}")
            return None

    edited = solid

    total_added = 0.0
    total_removed = 0.0

    # Two-support first, then trihedral (sphere/bspline)
    two_support_first = []
    trihedral_later = []
    for idx, f in resolved_targets:
        gt = geom_type(f)
        if gt in ("SPHERE", "BSPLINE"):
            trihedral_later.append((idx, f))
        else:
            two_support_first.append((idx, f))

    print(f"INFO: processing order two-support={len(two_support_first)} trihedral={len(trihedral_later)}")

    def process_target(idx, f):
        nonlocal edited, total_added, total_removed

        gt = geom_type(f)
        needed = 3 if gt in ("SPHERE", "BSPLINE") else 2
        supports = collect_support_planes(idx, needed=needed)
        print(f"SELECTED: {len(supports)} planar support faces for target face#{idx} type={gt}   idx={[i for i,_,_ in supports]}")
        for (pi, pf, sc) in supports:
            print(f"  SUPPORT: face#{pi} type={geom_type(pf)} shared_edge_len_score={sc:.3f} center={vfmt(pf.Center())} n={vfmt(pf.normalAt())}")

        if len(supports) < needed:
            print(f"WARN: insufficient supports for face#{idx} (have {len(supports)}, need {needed}) -> skipping")
            return

        R = rmap.get(idx, 8.0)
        pad = max(2.0, 0.8 * R + 1.0)

        wedge = build_corner_patch(idx, f, supports, pad=pad)
        if wedge is None:
            print(f"CHECK: face#{idx} produced no wedge -> skipping")
            return

        # Decide fuse vs cut based on where the wedge lies relative to current solid
        try:
            add_shape = wedge.cut(edited)  # outside solid region that would be added
            add_shape = safe_clean(add_shape)
            add_vol = add_shape.Volume() if add_shape is not None else 0.0
        except Exception as e:
            print(f"ERROR: computing add_shape failed for face#{idx}: {e}")
            return

        try:
            cut_res = edited.cut(wedge)
            cut_res = safe_clean(cut_res)
            rem_vol = edited.Volume() - cut_res.Volume()
        except Exception:
            rem_vol = 0.0

        print(f"CHECK: face#{idx} candidate volumes add_vol={add_vol:.3f} removed_if_cut={rem_vol:.3f} (heuristic)")

        # Guard against runaway tools: the intent is LOCAL corner patches.
        # If add_vol is extremely large, skip rather than resculpt the part.
        if add_vol > 250000.0:
            print(f"WARN: face#{idx} add_vol {add_vol:.3f} too large for localized patch -> skipping")
            return

        # Prefer fuse for exterior de-rounding; only cut if clearly more sensible.
        do_fuse = True
        if rem_vol > add_vol * 1.5 and rem_vol > 1.0:
            do_fuse = False

        if do_fuse:
            if add_vol < 1e-6:
                print(f"CHECK: face#{idx} add_vol ~0 -> no-op, skipping")
                return
            try:
                # Ensure added material also cannot exceed measured envelope
                add_shape = add_shape.intersect(global_clip)
                add_shape = add_shape.cut(protect)
                add_shape = safe_clean(add_shape)

                abb = add_shape.BoundingBox()
                print(f"CHECK: face#{idx} added bbox {bbfmt(abb)}")
                print(
                    "VERIFY: added vs measured envelope deltas "
                    f"dxmin={abb.xmin - gx0:+.3f} dxmax={abb.xmax - gx1:+.3f} "
                    f"dymin={abb.ymin - gy0:+.3f} dymax={abb.ymax - gy1:+.3f} "
                    f"dzmin={abb.zmin - gz0:+.3f} dzmax={abb.zmax - gz1:+.3f}"
                )

                edited = safe_clean(edited.fuse(add_shape))
                total_added += add_vol
                print(f"APPLIED: fuse corner patch for face#{idx}  add_vol={add_vol:.3f}")
            except Exception as e:
                print(f"ERROR: fuse failed for face#{idx}: {e}")
        else:
            # Cutting is not expected for this sub-goal; keep it conservative.
            if rem_vol < 1e-6:
                print(f"CHECK: face#{idx} rem_vol ~0 -> no-op, skipping")
                return
            if rem_vol > 250000.0:
                print(f"WARN: face#{idx} rem_vol {rem_vol:.3f} too large for localized patch -> skipping")
                return
            try:
                edited = safe_clean(edited.cut(wedge))
                total_removed += rem_vol
                print(f"APPLIED: cut corner patch for face#{idx}  removed_vol={rem_vol:.3f}")
            except Exception as e:
                print(f"ERROR: cut failed for face#{idx}: {e}")

    # Two-support first
    for idx, f in two_support_first:
        process_target(idx, f)

    # Trihedral (sphere/bspline)
    for idx, f in trihedral_later:
        process_target(idx, f)

    # ---- Final diagnostics ----
    ebb = edited.BoundingBox()
    vole = edited.Volume()
    print(f"RESULT: edited bbox {bbfmt(ebb)}")
    print(
        "VERIFY: bbox vs measured exact "
        f"xmin Δ={ebb.xmin - gx0:+.6f} xmax Δ={ebb.xmax - gx1:+.6f} "
        f"ymin Δ={ebb.ymin - gy0:+.6f} ymax Δ={ebb.ymax - gy1:+.6f} "
        f"zmin Δ={ebb.zmin - gz0:+.6f} zmax Δ={ebb.zmax - gz1:+.6f}"
    )
    print(f"RESULT: volume before={vol0:.3f} after={vole:.3f} delta={vole - vol0:+.3f} mm^3")
    print(f"RESULT: total_added_requested={total_added:.3f} total_removed_requested={total_removed:.3f}")

    # Surface-type counts to help spot residual exterior blends (holes will remain as cylinders)
    try:
        ftypes = {}
        for f in edited.Faces():
            gt = geom_type(f)
            ftypes[gt] = ftypes.get(gt, 0) + 1
        print(f"INFO: edited face type counts: {dict(sorted(ftypes.items()))}")
    except Exception as e:
        print(f"WARN: could not compute edited face type counts: {e}")

    # Ensure single connected solid
    try:
        esols = edited.Solids()
        print(f"INFO: edited solids={len(esols)}")
        if len(esols) == 1:
            return edited
        # If boolean produced multiple, re-compound them (but keep connectedness expectation visible)
        out = cq.Compound.makeCompound([s for s in esols])
        return out
    except Exception as e:
        print(f"WARN: could not verify solids count: {e}")
        return edited