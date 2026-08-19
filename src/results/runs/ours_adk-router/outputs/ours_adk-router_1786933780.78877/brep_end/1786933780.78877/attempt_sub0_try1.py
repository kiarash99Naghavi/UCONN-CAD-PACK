def my_cad_function(args):
    import cadquery as cq
    from math import isfinite

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input

    base = shape.val() if hasattr(shape, "val") else shape
    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) != 1:
        print("WARNING: expected 1 solid; will edit solid[0] and re-compound the rest")
    base_solid = sols[0]

    orig_bb = base_solid.BoundingBox()
    print(f"ORIG BBOX: min=({orig_bb.xmin:.3f},{orig_bb.ymin:.3f},{orig_bb.zmin:.3f}) max=({orig_bb.xmax:.3f},{orig_bb.ymax:.3f},{orig_bb.zmax:.3f})")

    # --- Resolve face #12 and verify against provided geometry index ---
    faces = base_solid.Faces()
    print(f"INFO: base_solid faces={len(faces)} edges={len(base_solid.Edges())}")
    f12 = faces[12]
    c12 = f12.Center()
    a12 = f12.Area()
    n12 = f12.normalAt().normalized()
    print(f"RESOLVED: face#12 center={[round(c12.x,3), round(c12.y,3), round(c12.z,3)]} area={a12:.3f} normal={[round(n12.x,3), round(n12.y,3), round(n12.z,3)]}")

    # Required inner-loop edge indices on face #12
    inner_edge_idx = [3, 5, 8, 11, 14, 17, 20, 22]
    all_edges = base_solid.Edges()
    inner_edges = [all_edges[i] for i in inner_edge_idx]
    print(f"SELECTED: {len(inner_edges)} edges for face#12 inner-loop rim idx={inner_edge_idx}")
    for i, e in zip(inner_edge_idx, inner_edges):
        ce = e.Center()
        print(f"  edge#{i}: len={e.Length():.3f} center=({ce.x:.3f},{ce.y:.3f},{ce.z:.3f})")

    # Build the inner loop wire
    inner_wire = None
    try:
        inner_wire = cq.Wire.assembleEdges(inner_edges)
        print(f"INFO: assembled inner wire. IsClosed={inner_wire.IsClosed()}")
    except Exception as ex:
        print(f"WARNING: Wire.assembleEdges failed: {ex}")

    # Fallback: pick smallest-area wire on face#12 (should be the opening loop)
    if inner_wire is None or not inner_wire.IsClosed():
        wlist = list(f12.Wires())
        print(f"INFO: face#12 wires={len(wlist)}")
        wire_areas = []
        for wi, w in enumerate(wlist):
            try:
                fa = cq.Face.makeFromWires(w).Area()
            except Exception:
                fa = float('nan')
            wire_areas.append(fa)
            print(f"  wire[{wi}] area={fa}")
        # choose smallest finite area
        finite = [(i, a) for i, a in enumerate(wire_areas) if isfinite(a)]
        if not finite:
            print("SELECTED: 0 valid wires on face#12 (BUG) -> returning original shape")
            return shape
        wi_min = min(finite, key=lambda t: t[1])[0]
        inner_wire = wlist[wi_min]
        print(f"SELECTED: wire[{wi_min}] as inner opening loop (smallest area)")

    # Original loop area (opening boundary in the plane)
    try:
        inner_face_tmp = cq.Face.makeFromWires(inner_wire)
        inner_area = inner_face_tmp.Area()
    except Exception as ex:
        print(f"ERROR: cannot make face from inner wire: {ex} -> returning original shape")
        return shape
    print(f"INFO: inner loop area={inner_area:.3f}")

    # --- Offset the inner wire 20 mm into the opening (choose sign by area reduction) ---
    offset_mm = 20.0
    cand = []
    for d in (offset_mm, -offset_mm):
        try:
            woffs = inner_wire.offset2D(d)  # returns list of wires
            print(f"INFO: offset2D({d}) returned {len(woffs)} wire(s)")
            for k, w in enumerate(woffs):
                try:
                    a = cq.Face.makeFromWires(w).Area()
                except Exception:
                    a = float('nan')
                cand.append((d, k, w, a))
                print(f"  cand d={d} k={k} area={a}")
        except Exception as ex:
            print(f"WARNING: offset2D({d}) failed: {ex}")

    finite_cand = [(d, k, w, a) for (d, k, w, a) in cand if isfinite(a)]
    if not finite_cand:
        print("SELECTED: 0 offset wires (BUG) -> returning original shape")
        return shape

    # Choose the candidate that is smaller than the original loop area if possible; else smallest area overall
    smaller = [t for t in finite_cand if t[3] < inner_area]
    chosen = min(smaller, key=lambda t: t[3]) if smaller else min(finite_cand, key=lambda t: t[3])
    d_ch, k_ch, offset_wire, offset_area = chosen
    print(f"SELECTED: offset wire d={d_ch} k={k_ch} offset_area={offset_area:.3f} (target: inward by 20mm)")

    # Verify achieved inward width ~20 mm: min distance between wires
    try:
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape
        dist = BRepExtrema_DistShapeShape(inner_wire.wrapped, offset_wire.wrapped)
        dist.Perform()
        dmin = dist.Value()
        print(f"VERIFY: inward offset width (min wire-wire distance) = {dmin:.3f} mm (delta {dmin-offset_mm:+.3f} vs 20.000)")
    except Exception as ex:
        print(f"WARNING: could not compute wire-wire distance for offset verification: {ex}")

    # --- Create ledge face as a ring and extrude 5mm into body along -normal(face#12) ---
    try:
        ledge_face = cq.Face.makeFromWires(inner_wire, [offset_wire])
    except Exception as ex:
        print(f"ERROR: cannot make ledge ring face: {ex} -> returning original shape")
        return shape

    thickness_mm = 5.0
    extrude_vec = cq.Vector(-n12.x, -n12.y, -n12.z).normalized().multiply(thickness_mm)
    print(f"INFO: face#12 outward normal={list(map(lambda x: round(x,3), [n12.x,n12.y,n12.z]))}")
    print(f"INFO: extrude direction (into body)={list(map(lambda x: round(x,3), [extrude_vec.x, extrude_vec.y, extrude_vec.z]))} length={extrude_vec.Length:.3f} mm")

    ledge_solid = cq.Solid.extrudeLinear(ledge_face, extrude_vec)

    # Fuse into the base solid
    out_solid = base_solid.fuse(ledge_solid)

    # Isolate added material for self-checks
    try:
        added = out_solid.cut(base_solid)
        add_bb = added.BoundingBox()
        add_c = added.Center()
        print(f"ADDED: bbox min=({add_bb.xmin:.3f},{add_bb.ymin:.3f},{add_bb.zmin:.3f}) max=({add_bb.xmax:.3f},{add_bb.ymax:.3f},{add_bb.zmax:.3f})")
        print(f"ADDED: center=({add_c.x:.3f},{add_c.y:.3f},{add_c.z:.3f})")
    except Exception as ex:
        added = None
        print(f"WARNING: could not isolate added material via cut(out, base): {ex}")

    # Verify underside coplanarity with face#12 and thickness ~5mm
    # Plane for face#12: n12 dot (p - c12) = 0
    def signed_dist_to_f12_plane(p):
        v = cq.Vector(p.x - c12.x, p.y - c12.y, p.z - c12.z)
        return n12.dot(v)

    if added is not None:
        planar = [fa for fa in added.Faces() if fa.geomType() == "PLANE"]
        print(f"SELECTED: {len(planar)} planar faces on added ledge for verification")
        parallel = []
        for fa in planar:
            nn = fa.normalAt().normalized()
            # parallel if |dot| ~ 1
            dpar = abs(nn.dot(n12))
            if dpar > 0.999:
                sc = fa.Center()
                sd = signed_dist_to_f12_plane(sc)
                parallel.append((fa, sd, nn.dot(n12)))
        print(f"SELECTED: {len(parallel)} planar faces on added ledge parallel to face#12")
        for i, (_, sd, dotv) in enumerate(parallel):
            print(f"  parallel[{i}]: signed_dist_to_f12_plane={sd:.4f} mm  (dot_with_n12={dotv:.4f})")

        # Underside should have sd ~ 0; top should have sd ~ -5 (since extruded along -n12)
        if parallel:
            underside = min(parallel, key=lambda t: abs(t[1]))
            topface = min(parallel, key=lambda t: abs(t[1] + thickness_mm))
            print(f"VERIFY: underside coplanarity |dist|={abs(underside[1]):.4f} mm (target 0)")
            print(f"VERIFY: thickness via top plane dist={abs(topface[1] + thickness_mm):.4f} mm (target 0; top should be at -5mm)")
            # Also report measured thickness as separation along n12
            measured_t = abs(topface[1] - underside[1])
            print(f"VERIFY: measured ledge thickness (plane separation) = {measured_t:.4f} mm (delta {measured_t-thickness_mm:+.4f} vs 5.000)")
        else:
            print("WARNING: no parallel planar faces found on added ledge (unexpected)")

    # Verify unchanged outer bounding box
    new_bb = out_solid.BoundingBox()
    print(f"NEW  BBOX: min=({new_bb.xmin:.3f},{new_bb.ymin:.3f},{new_bb.zmin:.3f}) max=({new_bb.xmax:.3f},{new_bb.ymax:.3f},{new_bb.zmax:.3f})")
    print(
        "VERIFY: bbox delta "
        f"xmin {new_bb.xmin-orig_bb.xmin:+.3f}, xmax {new_bb.xmax-orig_bb.xmax:+.3f}, "
        f"ymin {new_bb.ymin-orig_bb.ymin:+.3f}, ymax {new_bb.ymax-orig_bb.ymax:+.3f}, "
        f"zmin {new_bb.zmin-orig_bb.zmin:+.3f}, zmax {new_bb.zmax-orig_bb.zmax:+.3f}"
    )

    # Return as single solid or re-compounded if necessary
    if len(sols) == 1:
        return out_solid

    rest = [s for i, s in enumerate(sols) if i != 0]
    return cq.Compound.makeCompound(rest + [out_solid])