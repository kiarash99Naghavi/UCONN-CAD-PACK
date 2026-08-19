def my_cad_function(args):
    import cadquery as cq
    from collections import Counter

    shape = cq.importers.importStep(args["input_file"])   # ALWAYS load the input
    base = shape.val() if hasattr(shape, "val") else shape

    def vfmt(v):
        return f"({v.x:.3f},{v.y:.3f},{v.z:.3f})"

    # Numbers / entities named by the sub-goal
    remove_face_indices = [
        2, 6, 9, 23, 25, 27, 28, 29, 35, 36, 37,  # cylindrical blend faces (per instruction)
        0, 4,                                      # conical chamfer faces
        21, 22,                                    # BSPLINE corner faces
        30, 33                                     # spherical corner faces
    ]
    preserve_face_index = 20
    preserve_opening_r = 14.1421

    print("NUMBERS: face indices to REMOVE =", remove_face_indices)
    print(f"NUMBERS: face index to PRESERVE (do not target) = #{preserve_face_index}")
    print(f"NUMBERS: preserve bore/opening radius r={preserve_opening_r:.4f} mm")

    sols = base.Solids()
    print(f"INFO: imported solids={len(sols)}")
    if len(sols) < 1:
        print("SELECTED: 0 solids (ERROR)")
        return shape

    solid = sols[0]
    bb0 = solid.BoundingBox()
    print(
        "INFO: base bbox "
        f"min=({bb0.xmin:.3f},{bb0.ymin:.3f},{bb0.zmin:.3f}) "
        f"max=({bb0.xmax:.3f},{bb0.ymax:.3f},{bb0.zmax:.3f})"
    )

    faces0 = solid.Faces()
    edges0 = solid.Edges()
    print(f"INFO: base faces={len(faces0)} edges={len(edges0)}")

    # Surface-type mix pre
    try:
        mix0 = Counter([f.geomType() for f in faces0])
        print("INFO: base face geomType mix:", dict(mix0))
    except Exception as e:
        print(f"WARN: could not compute geomType mix (base): {e}")

    # Resolve and validate faces-by-index (diagnostic)
    # Note: indices are per the geometry index; after prior edits they should still be resolvable,
    # but we guard for out-of-range.
    to_remove_faces = []
    resolved_remove_idx = []

    # Preserve face diagnostic
    if preserve_face_index < len(faces0):
        fp = faces0[preserve_face_index]
        print(
            f"INFO: resolved PRESERVE face #{preserve_face_index} "
            f"geom={fp.geomType()} area={fp.Area():.3f} center={vfmt(fp.Center())}"
        )
    else:
        print(
            f"WARN: preserve face #{preserve_face_index} out of range for this solid "
            f"(faces={len(faces0)}). Will preserve by radius-check instead."
        )

    # Gather faces to remove
    for idx in remove_face_indices:
        if idx == preserve_face_index:
            print(f"SKIP: face #{idx} is marked PRESERVE")
            continue
        if idx < 0 or idx >= len(faces0):
            print(f"WARN: face #{idx} not present (faces={len(faces0)}); cannot target by index")
            continue
        f = faces0[idx]
        try:
            gt = f.geomType()
        except Exception:
            gt = "(unknown)"
        print(
            f"INFO: resolved REMOVE face #{idx} geom={gt} area={f.Area():.3f} "
            f"center={vfmt(f.Center())}"
        )
        to_remove_faces.append(f)
        resolved_remove_idx.append(idx)

    print(f"SELECTED: {len(to_remove_faces)} faces for defeaturing removal   idx={resolved_remove_idx}")
    if len(to_remove_faces) == 0:
        print("SELECTED: 0 faces to remove (NO-OP) -- returning input unchanged")
        return shape

    # Defeaturing (remove faces + heal)
    try:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
        from OCP.TopTools import TopTools_ListOfShape

        lof = TopTools_ListOfShape()
        for f in to_remove_faces:
            lof.Append(f.wrapped)

        df = BRepAlgoAPI_Defeaturing(solid.wrapped)
        df.AddFacesToRemove(lof)
        df.Build()
        if not df.IsDone():
            print("ERROR: BRepAlgoAPI_Defeaturing reported IsDone()=False")
            return shape

        edited = cq.Shape.cast(df.Shape())
        # Ensure we are still working with a solid (or at least a shape)
        print("INFO: defeaturing succeeded")

    except Exception as e:
        print(f"ERROR: defeaturing failed or API unavailable: {e}")
        return shape

    # Post checks
    try:
        bb1 = edited.BoundingBox()
        print(
            "CHECK: edited bbox "
            f"min=({bb1.xmin:.3f},{bb1.ymin:.3f},{bb1.zmin:.3f}) "
            f"max=({bb1.xmax:.3f},{bb1.ymax:.3f},{bb1.zmax:.3f})"
        )
    except Exception as e:
        print(f"WARN: could not compute edited bbox: {e}")

    try:
        faces1 = edited.Faces()
        edges1 = edited.Edges()
        print(f"INFO: edited faces={len(faces1)} edges={len(edges1)}")
        mix1 = Counter([f.geomType() for f in faces1])
        print("INFO: edited face geomType mix:", dict(mix1))
    except Exception as e:
        print(f"WARN: could not compute edited face/edge stats: {e}")

    # Bore/opening preservation check: ensure we still have circular edges at r=14.1421
    # (do not assume indices survive; check by geometry)
    rtol = 0.05
    bore_edge_idx = []
    try:
        for i, e in enumerate(edited.Edges()):
            try:
                if e.geomType() == "CIRCLE":
                    r = e.radius()
                    if abs(r - preserve_opening_r) <= rtol:
                        bore_edge_idx.append(i)
            except Exception:
                pass
        print(
            f"SELECTED: {len(bore_edge_idx)} circular edges with r≈{preserve_opening_r:.4f}±{rtol:.2f} "
            f"(bore/opening witness)  idx={bore_edge_idx[:30]}{'...' if len(bore_edge_idx)>30 else ''}"
        )
        if len(bore_edge_idx) == 0:
            print("WARN: did not detect any r=14.1421 circular edges after defeaturing; bore may have been affected")
    except Exception as e:
        print(f"WARN: could not scan edges for bore radius preservation: {e}")

    # If multi-solid, re-compound untouched others
    if len(sols) == 1:
        return edited

    out = cq.Compound.makeCompound([s for i, s in enumerate(sols) if i != 0] + [edited])
    return out