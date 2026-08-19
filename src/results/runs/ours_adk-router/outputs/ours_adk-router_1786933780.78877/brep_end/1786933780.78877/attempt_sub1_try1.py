def my_cad_function(args):
    import cadquery as cq
    from math import isfinite
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("ERROR: no solids found -> returning original")
        return shape

    base_solid = sols[0]
    orig_bb = base_solid.BoundingBox()
    print(f"ORIG BBOX: min=({orig_bb.xmin:.3f},{orig_bb.ymin:.3f},{orig_bb.zmin:.3f}) max=({orig_bb.xmax:.3f},{orig_bb.ymax:.3f},{orig_bb.zmax:.3f})")

    # --- Numbers named by the sub-goal ---
    fillet_r = 2.0  # mm
    ledge_thickness = 5.0  # mm (already exists; used to find the upper junction plane)
    print(f"INFO: target fillet radius={fillet_r:.3f} mm")
    print(f"INFO: expected ledge thickness reference={ledge_thickness:.3f} mm")

    # --- Find reference bottom face (face#12 in the original index) by its normal direction ---
    n12_ref = cq.Vector(0.0, -0.259, -0.966).normalized()

    planar_faces = [f for f in base_solid.Faces() if f.geomType() == "PLANE"]
    print(f"INFO: planar faces in current solid={len(planar_faces)}")

    bottom_cands = []
    for f in planar_faces:
        nn = f.normalAt().normalized()
        d = abs(nn.dot(n12_ref))
        if d > 0.999:
            bottom_cands.append((f, d, f.Area(), f.Center(), nn))

    print(f"SELECTED: {len(bottom_cands)} planar faces matching face#12 normal family for bottom reference")
    for i, (_, d, a, c, nn) in enumerate(sorted(bottom_cands, key=lambda t: -t[2])[:10]):
        print(
            f"  cand[{i}]: area={a:.3f} dot_ref={d:.6f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) normal=({nn.x:.3f},{nn.y:.3f},{nn.z:.3f})"
        )

    if not bottom_cands:
        print("ERROR: could not find bottom reference face (face#12 family) -> returning original")
        return shape

    # pick largest-area candidate as the bottom reference plane
    f_bottom, _, a_bottom, c_bottom, n_bottom = max(bottom_cands, key=lambda t: t[2])
    print(
        "RESOLVED: bottom reference face (face#12 family) "
        f"area={a_bottom:.3f} center=({c_bottom.x:.3f},{c_bottom.y:.3f},{c_bottom.z:.3f}) normal=({n_bottom.x:.3f},{n_bottom.y:.3f},{n_bottom.z:.3f})"
    )

    # Also locate top-side associated face#34 family for context (not used as anchor)
    n34_ref = cq.Vector(0.0, 0.966, -0.259).normalized()
    top34_cands = []
    for f in planar_faces:
        nn = f.normalAt().normalized()
        d = abs(nn.dot(n34_ref))
        if d > 0.999:
            top34_cands.append((f, d, f.Area(), f.Center(), nn))

    print(f"SELECTED: {len(top34_cands)} planar faces matching face#34 normal family (top-side context)")
    if top34_cands:
        f34, d34, a34, c34, n34 = max(top34_cands, key=lambda t: t[2])
        print(
            "RESOLVED: top context face (face#34 family) "
            f"area={a34:.3f} dot_ref={d34:.6f} center=({c34.x:.3f},{c34.y:.3f},{c34.z:.3f}) normal=({n34.x:.3f},{n34.y:.3f},{n34.z:.3f})"
        )

    # signed distance to bottom plane: n_bottom dot (p - c_bottom)
    def sd_to_bottom_plane(p: cq.Vector) -> float:
        return n_bottom.dot(cq.Vector(p.x - c_bottom.x, p.y - c_bottom.y, p.z - c_bottom.z))

    # --- Find the ledge top planar face: parallel to bottom plane at signed distance ~ -5mm ---
    expected_top_center = cq.Vector(
        c_bottom.x - n_bottom.x * ledge_thickness,
        c_bottom.y - n_bottom.y * ledge_thickness,
        c_bottom.z - n_bottom.z * ledge_thickness,
    )
    print(
        "INFO: expected ledge top center approx at "
        f"({expected_top_center.x:.3f},{expected_top_center.y:.3f},{expected_top_center.z:.3f})"
    )

    top_cands = []
    for f in planar_faces:
        nn = f.normalAt().normalized()
        # parallel if |dot| ~ 1
        if abs(abs(nn.dot(n_bottom)) - 1.0) < 1e-3:
            c = f.Center()
            sd = sd_to_bottom_plane(c)
            if abs(sd + ledge_thickness) < 0.75:  # tolerance for locating the -5mm plane
                wcnt = len(list(f.Wires()))
                # prioritize the ledge top face: typically a ring -> 2 wires
                dist_to_expected = cq.Vector(c.x - expected_top_center.x, c.y - expected_top_center.y, c.z - expected_top_center.z).Length
                top_cands.append((f, f.Area(), c, nn, sd, wcnt, dist_to_expected))

    print(f"SELECTED: {len(top_cands)} planar faces near sd=-{ledge_thickness:.3f}mm (ledge top candidates)")
    for i, (_, a, c, nn, sd, wcnt, dexp) in enumerate(sorted(top_cands, key=lambda t: (abs(t[4] + ledge_thickness), -t[5], t[6]))[:10]):
        print(
            f"  top_cand[{i}]: area={a:.3f} sd={sd:.3f} wires={wcnt} dist_to_expected_center={dexp:.3f} "
            f"center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) normal=({nn.x:.3f},{nn.y:.3f},{nn.z:.3f})"
        )

    if not top_cands:
        print("ERROR: could not find ledge top face near -5mm plane -> returning original")
        return shape

    # pick best: closest sd to -5, prefer 2 wires, then closest to expected center
    def top_rank(t):
        f, a, c, nn, sd, wcnt, dexp = t
        return (abs(sd + ledge_thickness), 0 if wcnt == 2 else 1, dexp, -a)

    f_ledge_top, a_top, c_top, n_top, sd_top, wcnt_top, dexp_top = sorted(top_cands, key=top_rank)[0]
    print(
        "RESOLVED: ledge top face "
        f"area={a_top:.3f} sd={sd_top:.3f}mm (delta {sd_top + ledge_thickness:+.3f} vs -{ledge_thickness:.3f}) "
        f"wires={wcnt_top} center=({c_top.x:.3f},{c_top.y:.3f},{c_top.z:.3f})"
    )

    # --- Select ONLY the OUTER boundary wire edges of the ledge top face (junction with existing inner wall) ---
    wires = list(f_ledge_top.Wires())
    print(f"INFO: ledge top face wires={len(wires)}")
    if len(wires) < 1:
        print("ERROR: ledge top face has no wires -> returning original")
        return shape

    wire_infos = []
    for wi, w in enumerate(wires):
        try:
            a = cq.Face.makeFromWires(w).Area()
        except Exception:
            a = float('nan')
        ecount = len(list(w.Edges()))
        wire_infos.append((wi, w, a, ecount))
        print(f"  wire[{wi}]: area={a} edges={ecount}")

    finite_wires = [t for t in wire_infos if isfinite(t[2])]
    if not finite_wires:
        print("ERROR: no finite-area wires on ledge top face -> returning original")
        return shape

    wi_outer, outer_wire, outer_area, outer_ecount = max(finite_wires, key=lambda t: t[2])
    print(f"SELECTED: wire[{wi_outer}] as OUTER boundary of ledge top face (area={outer_area:.3f})")

    outer_edges = list(outer_wire.Edges())
    print(f"SELECTED: {len(outer_edges)} edges for upper junction edge loop (to fillet)")
    if not outer_edges:
        print("ERROR: 0 edges selected for fillet -> returning original")
        return shape

    sds = []
    for k, e in enumerate(outer_edges[:50]):
        ce = e.Center()
        sd = sd_to_bottom_plane(ce)
        sds.append(sd)
        print(f"  edge[{k}]: len={e.Length():.3f} center=({ce.x:.3f},{ce.y:.3f},{ce.z:.3f}) sd_to_bottom={sd:.3f}")
    if len(outer_edges) > 50:
        print(f"  ... showing 50 of {len(outer_edges)}")

    # sanity: ensure these are NOT on the bottom plane (we must keep all new bottom edges sharp)
    sd_min = min(sd_to_bottom_plane(e.Center()) for e in outer_edges)
    sd_max = max(sd_to_bottom_plane(e.Center()) for e in outer_edges)
    print(f"VERIFY: selected junction loop sd_to_bottom range = [{sd_min:.3f}, {sd_max:.3f}] mm (should be near -{ledge_thickness:.3f}, not near 0)")

    # --- Pre-verify existing radius~2 faces near bottom plane (should not change due to this operation) ---
    def radius2_faces_in_band(solid, sd_lo, sd_hi, tol_r=0.05):
        out = []
        for f in solid.Faces():
            try:
                ad = BRepAdaptor_Surface(f.wrapped, True)
                st = ad.GetType()
                rad = None
                if st == GeomAbs_Cylinder:
                    rad = ad.Cylinder().Radius()
                elif st == GeomAbs_Torus:
                    rad = ad.Torus().MinorRadius()
                if rad is None:
                    continue
                if abs(rad - fillet_r) <= tol_r:
                    c = f.Center()
                    sd = sd_to_bottom_plane(c)
                    if sd_lo <= sd <= sd_hi:
                        out.append((f, rad, c, sd, st))
            except Exception:
                continue
        return out

    pre_r2_near_bottom = radius2_faces_in_band(base_solid, -0.5, 0.5)
    print(f"VERIFY(pre): radius~{fillet_r:.3f} faces with center sd in [-0.5,+0.5] mm (bottom band) = {len(pre_r2_near_bottom)}")

    # --- Apply 2mm fillet to the upper junction edge loop ---
    try:
        out_solid = base_solid.fillet(fillet_r, edgeList=outer_edges)
        print(f"INFO: fillet({fillet_r}mm) applied to {len(outer_edges)} edges as one operation")
    except Exception as ex:
        print(f"ERROR: fillet failed on full edge set: {ex}")
        print("ATTEMPT: progressive fillet in smaller batches")
        out_solid = base_solid
        # Progressive batches; NOTE: edges get invalidated after each successful fillet, so we must re-select each time.
        # We'll re-acquire the ledge top face after each batch by re-running the same face-finding logic.
        batch_size = 4
        applied = 0
        for start in range(0, len(outer_edges), batch_size):
            # Re-find ledge top face and its outer-wire edges on the current out_solid
            planar_faces2 = [f for f in out_solid.Faces() if f.geomType() == "PLANE"]
            # bottom reference plane on out_solid: find best again
            bottom_cands2 = []
            for f in planar_faces2:
                nn = f.normalAt().normalized()
                d = abs(nn.dot(n12_ref))
                if d > 0.999:
                    bottom_cands2.append((f, d, f.Area(), f.Center(), nn))
            if not bottom_cands2:
                print("  PROGRESSIVE: could not re-find bottom plane -> stopping")
                break
            f_bottom2, _, _, c_bottom2, n_bottom2 = max(bottom_cands2, key=lambda t: t[2])

            def sd2(p: cq.Vector) -> float:
                return n_bottom2.dot(cq.Vector(p.x - c_bottom2.x, p.y - c_bottom2.y, p.z - c_bottom2.z))

            expected_top_center2 = cq.Vector(
                c_bottom2.x - n_bottom2.x * ledge_thickness,
                c_bottom2.y - n_bottom2.y * ledge_thickness,
                c_bottom2.z - n_bottom2.z * ledge_thickness,
            )

            top_cands2 = []
            for f in planar_faces2:
                nn = f.normalAt().normalized()
                if abs(abs(nn.dot(n_bottom2)) - 1.0) < 1e-3:
                    c = f.Center()
                    sdv = sd2(c)
                    if abs(sdv + ledge_thickness) < 0.75:
                        wcnt = len(list(f.Wires()))
                        dist_to_expected = cq.Vector(c.x - expected_top_center2.x, c.y - expected_top_center2.y, c.z - expected_top_center2.z).Length
                        top_cands2.append((f, f.Area(), c, nn, sdv, wcnt, dist_to_expected))
            if not top_cands2:
                print("  PROGRESSIVE: could not re-find ledge top face -> stopping")
                break

            f_top2, _, _, _, _, _, _ = sorted(top_cands2, key=lambda t: (abs(t[4] + ledge_thickness), 0 if t[5] == 2 else 1, t[6], -t[1]))[0]
            wires2 = list(f_top2.Wires())
            fin2 = []
            for w in wires2:
                try:
                    a = cq.Face.makeFromWires(w).Area()
                except Exception:
                    a = float('nan')
                if isfinite(a):
                    fin2.append((w, a))
            if not fin2:
                print("  PROGRESSIVE: no finite wires -> stopping")
                break
            outer_wire2 = max(fin2, key=lambda t: t[1])[0]
            outer_edges2 = list(outer_wire2.Edges())
            if not outer_edges2:
                print("  PROGRESSIVE: no outer edges -> stopping")
                break

            batch = outer_edges2[start : min(start + batch_size, len(outer_edges2))]
            print(f"  PROGRESSIVE: attempting fillet on batch edges [{start}:{start+len(batch)}] size={len(batch)}")
            try:
                out_solid = out_solid.fillet(fillet_r, edgeList=batch)
                applied += len(batch)
                print(f"  PROGRESSIVE: batch success; applied so far={applied}")
            except Exception as ex2:
                print(f"  PROGRESSIVE: batch failed: {ex2}")
                # continue to next batch; partial is better than none
                continue

        print(f"INFO: progressive fillet applied edges (attempted)={applied} (requested loop edges initial={len(outer_edges)})")

    # --- Verify bbox unchanged ---
    new_bb = out_solid.BoundingBox()
    print(f"NEW  BBOX: min=({new_bb.xmin:.3f},{new_bb.ymin:.3f},{new_bb.zmin:.3f}) max=({new_bb.xmax:.3f},{new_bb.ymax:.3f},{new_bb.zmax:.3f})")
    print(
        "VERIFY: bbox delta "
        f"xmin {new_bb.xmin-orig_bb.xmin:+.3f}, xmax {new_bb.xmax-orig_bb.xmax:+.3f}, "
        f"ymin {new_bb.ymin-orig_bb.ymin:+.3f}, ymax {new_bb.ymax-orig_bb.ymax:+.3f}, "
        f"zmin {new_bb.zmin-orig_bb.zmin:+.3f}, zmax {new_bb.zmax-orig_bb.zmax:+.3f}"
    )

    # --- Verify: report achieved radius~2 faces near the junction band and confirm no new bottom-band fillet ---
    post_r2_near_bottom = radius2_faces_in_band(out_solid, -0.5, 0.5)
    print(f"VERIFY(post): radius~{fillet_r:.3f} faces with center sd in [-0.5,+0.5] mm (bottom band) = {len(post_r2_near_bottom)} (delta {len(post_r2_near_bottom)-len(pre_r2_near_bottom):+d})")

    # Junction band: between bottom plane and ledge top plane (should catch the new fillet surfaces)
    post_r2_junction = radius2_faces_in_band(out_solid, -6.5, -0.5)
    print(f"VERIFY(post): radius~{fillet_r:.3f} faces with center sd in [-6.5,-0.5] mm (upper-junction band) = {len(post_r2_junction)}")
    for i, (_, rad, c, sdv, st) in enumerate(post_r2_junction[:20]):
        stname = "CYL" if st == GeomAbs_Cylinder else ("TORUS" if st == GeomAbs_Torus else str(st))
        print(f"  r2_face[{i}]: type={stname} rad={rad:.4f} center=({c.x:.3f},{c.y:.3f},{c.z:.3f}) sd_to_bottom={sdv:.3f}")
    if len(post_r2_junction) > 20:
        print(f"  ... showing 20 of {len(post_r2_junction)}")

    # --- Return as single solid or re-compounded if necessary ---
    if len(sols) == 1:
        return out_solid

    rest = [s for i, s in enumerate(sols) if i != 0]
    return cq.Compound.makeCompound(rest + [out_solid])