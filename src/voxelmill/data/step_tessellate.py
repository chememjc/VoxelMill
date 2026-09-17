"""Tessellate a STEP file to STL inside FreeCAD's embedded Python.

Invoked only via FreeCAD ``-c`` (see ``voxelmill.importers``). Arguments arrive
through ``FC_SCRIPT_ARGS`` (0x1F-joined); stdin is closed by the caller.

Under ``-c``, ``__name__`` is the script basename — there is no
``if __name__ == "__main__"`` guard. ``sys.exit`` is swallowed; use ``os._exit``.
"""
import json
import math
import os
import sys
import traceback


def log(msg):
    text = str(msg)
    try:
        import FreeCAD
        FreeCAD.Console.PrintMessage(text + "\n")
    except Exception:
        pass
    print(text, flush=True)


def fail(msg, code=1):
    log("ERROR: %s" % msg)
    os._exit(code)


def parse_args():
    raw = os.environ.get("FC_SCRIPT_ARGS")
    if raw is None:
        fail("FC_SCRIPT_ARGS not set — run through voxelmill.importers, not FreeCAD directly")
    argv = [a for a in raw.split("\x1f") if a != ""]
    import argparse
    ap = argparse.ArgumentParser(prog="step_tessellate")
    ap.add_argument("input", help="STEP (.step/.stp) path, or 'box' to write a test solid")
    ap.add_argument("output", help="destination STL (tessellate) or STEP (box)")
    ap.add_argument("linear_mm", type=float, nargs="?", default=0.1,
                    help="linear deflection in millimeters")
    ap.add_argument("angular_deg", type=float, nargs="?", default=15.0,
                    help="angular deflection in degrees")
    ap.add_argument("--box-size", nargs=3, type=float, metavar=("X", "Y", "Z"),
                    default=None, help="with input 'box': dimensions in mm")
    try:
        return ap.parse_args(argv)
    except SystemExit as exc:
        os._exit(int(exc.code or 0))


def write_box(path, size):
    import Part
    sx, sy, sz = size
    if min(sx, sy, sz) <= 0:
        fail("box dimensions must be positive")
    Part.makeBox(float(sx), float(sy), float(sz)).exportStep(str(path))
    log("VOXELMILL_STEP_REPORT %s" % json.dumps({
        "mode": "box",
        "output": str(path),
        "size_mm": [float(sx), float(sy), float(sz)],
    }, sort_keys=True))


def tessellate(step_path, stl_path, linear_mm, angular_deg):
    import FreeCAD
    import MeshPart
    import Part

    FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Document").SetBool(
        "CreateBackupFiles", False)

    if linear_mm <= 0 or not math.isfinite(linear_mm):
        fail("linear deflection must be a positive finite millimeter value")
    if angular_deg <= 0 or not math.isfinite(angular_deg):
        fail("angular deflection must be a positive finite degree value")

    shape = Part.Shape()
    try:
        shape.read(str(step_path))
    except Exception as exc:
        fail("failed to read STEP %s: %s" % (step_path, exc))
    if shape.isNull() or not shape.Faces:
        fail("STEP produced no faces: %s" % step_path)

    mesh = MeshPart.meshFromShape(
        Shape=shape,
        LinearDeflection=float(linear_mm),
        AngularDeflection=math.radians(float(angular_deg)),
    )
    if mesh.CountFacets <= 0:
        fail("tessellation produced no triangles")

    mesh.write(str(stl_path))
    if not os.path.isfile(stl_path) or os.path.getsize(stl_path) <= 0:
        fail("STL was not written: %s" % stl_path)

    bb = mesh.BoundBox
    version = None
    try:
        version = list(FreeCAD.Version())
    except Exception:
        version = None

    report = {
        "mode": "tessellate",
        "input": str(step_path),
        "output": str(stl_path),
        "freecad_version": version,
        "linear_deflection_mm": float(linear_mm),
        "angular_deflection_deg": float(angular_deg),
        "triangle_count": int(mesh.CountFacets),
        "bounds": [
            [float(bb.XMin), float(bb.YMin), float(bb.ZMin)],
            [float(bb.XMax), float(bb.YMax), float(bb.ZMax)],
        ],
    }
    log("VOXELMILL_STEP_REPORT %s" % json.dumps(report, sort_keys=True))


def main():
    try:
        args = parse_args()
        out = os.path.abspath(args.output)
        parent = os.path.dirname(out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if args.input == "box":
            size = args.box_size or (10.0, 20.0, 30.0)
            write_box(out, size)
        else:
            tessellate(os.path.abspath(args.input), out, args.linear_mm, args.angular_deg)
        os._exit(0)
    except SystemExit as exc:
        os._exit(int(exc.code or 0))
    except Exception:
        log(traceback.format_exc())
        os._exit(1)


main()
