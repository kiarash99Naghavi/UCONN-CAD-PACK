def my_cad_function(args):
    import cadquery as cq
    from math import sqrt

    # --- Load ---
    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape
    solids = base.Solids()
    print(f"INFO: imported solids={len(solids)}")
    if len(solids) != 1:
        print("WARNING: expected 1 solid; proceeding with solid[0]")
    solid = solids[0]
    print(f"INFO: base faces={len(solid.Faces())} edges={len(solid.Edges())}")

    exp_min = (-949.62, -506.698, 26.8)
    exp_max = (-163.62, -338.409, 595.312)
    bb0 = solid.BoundingBox()
    print(
        "INFO: base bbox min="
        f"[{bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}] max="
        f"[{bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f}]"
    )
    print(f"INFO: expected bbox min={list(exp_min)} max={list(exp_max)}")

    # --- Resolve target faces by index (must match provided geometry index) ---
    target_face_idx = [21, 23, 44, 48]
    faces = solid.Faces()
    tgt_faces = []
    for fi in target_face_idx:
        f = faces[fi]
        c = f.Center()
        try:
            gt = f.geomType()
        except Exception:
            gt = "(unknown)"
        print(
            f"INFO: target face#{fi} geom={gt} area={f.Area():.3f} center="
            f"[{c.x:.3f},{c.y:.3f},{c.z:.3f}]"
        )
        tgt_faces.append(f)
    print(f"SELECTED: {len(tgt_faces)} faces for GREEN R63 corner family  idx={target_face_idx}")

    # --- Remove the 4 R63 corner faces using OCCT defeaturing (extends neighbors; restores continuity) ---
    solid_def = None
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing

        df = BRepAlgoAPI_Defeaturing()
        df.SetShape(solid.wrapped)
        for fi, f in zip(target_face_idx, tgt_faces):
            df.AddFaceToRemove(f.wrapped)
            print(f"INFO: defeaturing will remove face#{fi}")
        df.Build()
        res = cq.Shape.cast(df.Shape())
        res_solids = res.Solids()
        print(f"INFO: defeaturing result solids={len(res_solids)}")
        if len(res_solids) >= 1:
            # If it returned a compound with multiple solids, take the largest (should be 1)
            res_solids_sorted = sorted(res_solids, key=lambda s: s.Volume(), reverse=True)
            solid_def = res_solids_sorted[0]
            if len(res_solids) != 1:
                print("WARNING: defeaturing produced multiple solids; keeping the largest one")
        else:
            print("ERROR: defeaturing produced 0 solids")
    except Exception as e:
        print(f"ERROR: defeaturing failed: {e}")

    if solid_def is None:
        # Last-resort: do not return unchanged; keep original but attempt a direct fillet edit anyway.
        solid_def = solid
        print("WARNING: proceeding without defeaturing (fallback path)")

    bb1 = solid_def.BoundingBox()
    print(
        "INFO: after defeaturing bbox min="
        f"[{bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}] max="
        f"[{bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f}]"
    )

    # --- Find the 4 sharp corner edges (near the original green face centers) to fillet to R50 ---
    ref_centers = {
        21: (-186.513, -495.427, 50.289),
        23: (-926.727, -495.427, 50.289),
        44: (-186.513, -355.609, 572.096),
        48: (-926.727, -355.609, 572.096),
    }

    edges1 = solid_def.Edges()
    print(f"INFO: candidate edges after defeaturing count={len(edges1)}")

    # Use robust distance-to-vertex if available
    def edge_dist_to_point(edge, pt_xyz):
        vx = cq.Vertex.makeVertex(pt_xyz[0], pt_xyz[1], pt_xyz[2])
        try:
            from OCP.BRepExtrema import BRepExtrema_DistShapeShape

            dss = BRepExtrema_DistShapeShape(edge.wrapped, vx.wrapped)
            try:
                dss.Perform()
            except Exception:
                pass
            return float(dss.Value())
        except Exception:
            # fallback: center distance
            c = edge.Center()
            dx = c.x - pt_xyz[0]
            dy = c.y - pt_xyz[1]
            dz = c.z - pt_xyz[2]
            return sqrt(dx * dx + dy * dy + dz * dz)

    picked_edges = []
    picked_edge_ids = []
    for fi in target_face_idx:
        ref = ref_centers[fi]
        best_i = None
        best_e = None
        best_d = 1e99
        for i, e in enumerate(edges1):
            # We expect the corner "spine" after defeaturing to be a LINE edge
            gt = None
            try:
                gt = e.geomType()
            except Exception:
                gt = None
            if gt is not None and gt != "LINE":
                continue
            try:
                if e.Length() < 20.0:
                    continue
            except Exception:
                pass
            d = edge_dist_to_point(e, ref)
            if d < best_d:
                best_d = d
                best_i = i
                best_e = e
        if best_e is None:
            print(f"SELECTED: 0 edges near ref for corner face#{fi} (this is a bug)")
            continue
        c = best_e.Center()
        try:
            L = best_e.Length()
        except Exception:
            L = float('nan')
        print(
            f"SELECTED: 1 edge for corner face#{fi}  edge_list_idx={best_i} geom={best_e.geomType()} "
            f"len={L:.3f}  center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}]  dist_to_ref={best_d:.3f}"
        )
        picked_edges.append(best_e)
        picked_edge_ids.append(best_i)

    # Deduplicate (sometimes nearest-to-point can pick same edge if something went wrong)
    uniq_edges = []
    uniq_ids = []
    seen = set()
    for i, e in zip(picked_edge_ids, picked_edges):
        if i in seen:
            continue
        seen.add(i)
        uniq_edges.append(e)
        uniq_ids.append(i)
    print(f"SELECTED: {len(uniq_edges)} unique edges for R50 corner fillet  edge_list_idx={uniq_ids}")

    # --- Apply fillet R50 on those edges ---
    out = solid_def
    fillet_r = 50.0
    applied = 0
    if len(uniq_edges) > 0:
        try:
            out = out.fillet(fillet_r, uniq_edges)
            applied = len(uniq_edges)
            print(f"APPLIED: fillet R{fillet_r} on {applied} edges (batch)")
        except Exception as e:
            print(f"WARNING: batch fillet failed: {e}")
            # fallback: one-by-one
            out2 = out
            for i, ed in zip(uniq_ids, uniq_edges):
                try:
                    out2 = out2.fillet(fillet_r, [ed])
                    applied += 1
                    print(f"APPLIED: fillet R{fillet_r} on 1 edge (edge_list_idx={i})")
                except Exception as e2:
                    print(f"ERROR: fillet failed on edge_list_idx={i}: {e2}")
            out = out2
    else:
        print("ERROR: no edges selected for fillet; returning defeatured shape (will not meet R50 requirement)")

    # --- Verify: bbox unchanged, single solid, and corners now show R~50 cylinders near the 4 ref centers ---
    out_bb = out.BoundingBox()
    print(
        "VERIFY: output bbox min="
        f"[{out_bb.xmin:.3f},{out_bb.ymin:.3f},{out_bb.zmin:.3f}] max="
        f"[{out_bb.xmax:.3f},{out_bb.ymax:.3f},{out_bb.zmax:.3f}]"
    )
    print(
        "VERIFY: expected bbox min="
        f"{list(exp_min)} max={list(exp_max)}  dmin="
        f"[{out_bb.xmin-exp_min[0]:.6f},{out_bb.ymin-exp_min[1]:.6f},{out_bb.zmin-exp_min[2]:.6f}] dmax="
        f"[{out_bb.xmax-exp_max[0]:.6f},{out_bb.ymax-exp_max[1]:.6f},{out_bb.zmax-exp_max[2]:.6f}]"
    )

    out_solids = out.Solids()
    print(f"VERIFY: output solids={len(out_solids)}")

    # Radius verification near the 4 original green face centers
    try:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder

        out_faces = out.Faces()
        print(f"INFO: output faces={len(out_faces)} edges={len(out.Edges())}")

        def nearest_cyl(ref_xyz, r_target, r_tol=0.6):
            best = None
            best_d = 1e99
            for f in out_faces:
                ad = BRepAdaptor_Surface(f.wrapped)
                if ad.GetType() != GeomAbs_Cylinder:
                    continue
                r = float(ad.Cylinder().Radius())
                if abs(r - r_target) > r_tol:
                    continue
                c = f.Center()
                dx = c.x - ref_xyz[0]
                dy = c.y - ref_xyz[1]
                dz = c.z - ref_xyz[2]
                d = sqrt(dx * dx + dy * dy + dz * dz)
                if d < best_d:
                    best_d = d
                    best = (r, c)
            return best, best_d

        hits50 = 0
        leftover63 = 0
        for fi in target_face_idx:
            ref = ref_centers[fi]
            best50, d50 = nearest_cyl(ref, 50.0, r_tol=0.8)
            best63, d63 = nearest_cyl(ref, 63.0, r_tol=0.8)

            if best50 is None:
                print(f"VERIFY: ref corner face#{fi} -> found 0 nearby CYL faces with R~50")
            else:
                r, c = best50
                print(
                    f"VERIFY: ref corner face#{fi} -> nearest CYL R50: R={r:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}] dist={d50:.3f}"
                )
                hits50 += 1

            if best63 is not None and d63 < 10.0:
                r, c = best63
                print(
                    f"VERIFY: ref corner face#{fi} -> WARNING nearby CYL R63 still present: R={r:.3f} center=[{c.x:.3f},{c.y:.3f},{c.z:.3f}] dist={d63:.3f}"
                )
                leftover63 += 1
            else:
                print(f"VERIFY: ref corner face#{fi} -> no nearby CYL R63 within 10mm (good)")

        print(f"VERIFY: matched {hits50}/4 corners to CYL R~50")
        print(f"VERIFY: corners with nearby leftover R~63 cylinders (should be 0): {leftover63}")

    except Exception as e:
        print(f"WARNING: radius verification skipped/failed: {e}")

    return out