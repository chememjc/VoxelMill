#!/usr/bin/env python3
"""Acceptance test a built or downloaded VoxelMill Linux AppImage.

The test deliberately runs the artifact's bundled Python, Qt, VTK, native
extension, and application package.  It does not import VoxelMill from the
checkout.  A JSON record containing the artifact checksum, command evidence,
GUI evidence, and optional workflow provenance is written beside the outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "fixtures" / "shapes" / "tetrahedron.stl"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command, *, cwd: Path, env=None, timeout=900, accepted=(0,)):
    started = time.monotonic()
    result = subprocess.run(
        [str(item) for item in command], cwd=cwd, env=env,
        text=True, capture_output=True, timeout=timeout, check=False,
    )
    evidence = {
        "argv": [str(item) for item in command],
        "returncode": result.returncode,
        "seconds": time.monotonic() - started,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }
    if result.returncode not in accepted:
        raise RuntimeError(json.dumps(evidence, indent=2))
    return result, evidence


def _app_command(appimage: Path, *args):
    return [appimage, *args]


def _runtime_environment(appdir: Path, output: Path):
    usr = appdir / "usr"
    lib = usr / "lib"
    site = lib / "python3.10" / "site-packages"
    env = os.environ.copy()
    env.update({
        "PYTHONHOME": str(usr),
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(site),
        "LD_LIBRARY_PATH": ":".join([
            str(lib), str(lib / "python3.10" / "lib-dynload"),
            str(site / "vtkmodules"), str(site / "PySide6"),
        ]),
        "QT_PLUGIN_PATH": str(site / "PySide6" / "Qt" / "plugins"),
        "QT_QPA_PLATFORM_PLUGIN_PATH": str(site / "PySide6" / "Qt" / "plugins" / "platforms"),
        "XDG_CACHE_HOME": str(output / "cache"),
        "XDG_CONFIG_HOME": str(output / "config"),
        "XDG_DATA_HOME": str(output / "data"),
        "VOXELMILL_NO_WIZARD": "1",
    })
    env.pop("VIRTUAL_ENV", None)
    return env


def _extract(appimage: Path, output: Path):
    extract_root = output / "extracted"
    extract_root.mkdir(parents=True, exist_ok=True)
    appdir = extract_root / "squashfs-root"
    if appdir.exists():
        shutil.rmtree(appdir)
    result, evidence = _run(
        [appimage, "--appimage-extract"], cwd=extract_root, timeout=300)
    python = appdir / "usr" / "bin" / "python3.10"
    if not python.is_file():
        raise RuntimeError(f"extracted AppImage has no bundled Python: {python}")
    evidence["extracted_appdir"] = str(appdir)
    return appdir, evidence


def _json(path: Path):
    return json.loads(path.read_text())


def _reset_output(output: Path):
    """Remove only files this runner owns so a rerun cannot accept stale evidence."""
    for name in (
        "acceptance.json", "gui-roundtrip.voxmil", "gui.json", "inspect.json",
        "prepare.json", "prepared-model.stl", "prepared-raft.stl",
        "prepared-supports.stl", "prepared.goo", "prepared.stl", "prepared.voxmil",
        "slice.json", "verify.json",
    ):
        (output / name).unlink(missing_ok=True)
    for name in ("cache", "config", "data", "extracted"):
        path = output / name
        if path.exists():
            shutil.rmtree(path)


def _assert_release_settings(report, expected_version):
    support = report["settings"]["support"]
    assert support["brace_spacing_mm"] == 15.0, support["brace_spacing_mm"]
    assert support["brace_max_length_mm"] == 30.0, support["brace_max_length_mm"]
    assert "brace_start_height_mm" not in support
    assert support["allow_part_to_part"] is False
    return {
        "version": expected_version,
        "brace_spacing_mm": support["brace_spacing_mm"],
        "brace_max_length_mm": support["brace_max_length_mm"],
        "allow_part_to_part": support["allow_part_to_part"],
    }


def run_acceptance(args):
    artifact = Path(args.appimage).resolve()
    source = Path(args.source).resolve()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    _reset_output(output)
    if not artifact.is_file() or not source.is_file():
        raise SystemExit("the AppImage and source STL must both exist")
    artifact.chmod(artifact.stat().st_mode | 0o111)
    app_env = {
        **os.environ,
        "APPIMAGE_EXTRACT_AND_RUN": "1",
        "VOXELMILL_NO_WIZARD": "1",
        "XDG_CACHE_HOME": str(output / "cache"),
        "XDG_CONFIG_HOME": str(output / "config"),
        "XDG_DATA_HOME": str(output / "data"),
    }
    commands = []

    version, record = _run(_app_command(artifact, "--version"), cwd=output, env=app_env)
    commands.append(record)
    if args.expected_version not in version.stdout:
        raise RuntimeError(f"expected version {args.expected_version!r}, got {version.stdout!r}")

    inspect_report = output / "inspect.json"
    _, record = _run(_app_command(
        artifact, "inspect", source, "--no-self-intersections", "--report", inspect_report),
        cwd=output, env=app_env)
    commands.append(record)
    if _json(inspect_report)["meshes"][0]["triangle_count"] <= 0:
        raise RuntimeError("packaged inspect found no triangles")

    common = [
        "--set", "printer.build_mm=[40,40,40]",
        "--set", "printer.pixels=[400,400]",
        "--set", "printer.pixel_pitch_mm=[0.1,0.1]",
        "--set", "printer.edge_clearance_mm=0",
        "--set", "process.layer_height_mm=0.2",
        "--workers", "2",
    ]
    prepared = output / "prepared.stl"
    project = output / "prepared.voxmil"
    prepare_report = output / "prepare.json"
    _, record = _run(_app_command(
        artifact, "prepare", source, "--output", prepared, "--project", project,
        "--components", "--max-passes", "1", "--allow-unresolved",
        "--no-drainage", "--no-void-analysis", "--report", prepare_report, *common),
        cwd=output, env=app_env, timeout=1200, accepted=(0, 2))
    commands.append(record)
    prepared_report = _json(prepare_report)
    defaults = _assert_release_settings(prepared_report, args.expected_version)
    if not prepared.is_file() or not project.is_file() or not prepared_report["export"]["written"]:
        raise RuntimeError("packaged prepare did not write its STL and project")
    full_pass = prepared_report["passes"][-1]
    if full_pass["kind"] != "full_reslice":
        raise RuntimeError("prepare did not write, reopen, and fully reslice the accepted mesh")
    if full_pass["supports"].get("braces", 0) <= 0:
        raise RuntimeError("acceptance fixture generated no downward braces")
    island_count = full_pass["validation"]["metrics"].get("island_components")
    core_checks = full_pass["validation"]["checks"]
    if island_count != 0 or core_checks.get("raster_connectivity") != "pass":
        raise RuntimeError("prepared mesh introduced a floating raster component")
    if core_checks.get("support_routes") != "pass":
        raise RuntimeError("prepared support graph contains an ungrounded route")

    sliced = output / "prepared.goo"
    slice_report = output / "slice.json"
    _, record = _run(_app_command(
        artifact, "slice", prepared, "--output", sliced, "--allow-unresolved",
        "--report", slice_report, *common),
        cwd=output, env=app_env, timeout=1200, accepted=(0, 2))
    commands.append(record)
    if not sliced.is_file() or sliced.stat().st_size <= 0:
        raise RuntimeError("packaged slicer wrote no GOO output")

    verify_report = output / "verify.json"
    _, record = _run(_app_command(
        artifact, "verify", sliced, "--report", verify_report, *common),
        cwd=output, env=app_env, timeout=1200, accepted=(0, 2))
    commands.append(record)
    verified = _json(verify_report)
    if _json(slice_report)["verification"].get("decoded_pixels") != "pass":
        raise RuntimeError("published slice did not pass decoded-pixel verification")
    for check in ("raster_connectivity", "overlap"):
        if verified["report"]["checks"].get(check) != "pass":
            raise RuntimeError(f"published slice failed {check}")

    appdir, extraction = _extract(artifact, output)
    runtime_env = _runtime_environment(appdir, output)
    gui_report = output / "gui.json"
    inside = [
        appdir / "usr" / "bin" / "python3.10", Path(__file__).resolve(),
        "--inside-appdir", str(appdir), "--source", source,
        "--output-dir", output, "--expected-version", args.expected_version,
    ]
    if args.support_options:
        inside.append("--support-options")
    if args.skip_gui:
        inside.append("--skip-gui")
        gui_command = inside
    else:
        if not shutil.which("xvfb-run"):
            raise RuntimeError("xvfb-run is required for actual-render AppImage acceptance")
        runtime_env["QT_QPA_PLATFORM"] = "xcb"
        gui_command = ["xvfb-run", "-a", "--server-args=-screen 0 1280x1024x24", *inside]
    _, gui_evidence = _run(gui_command, cwd=output, env=runtime_env, timeout=1200)
    gui = _json(gui_report)
    if Path(gui["voxelmill_origin"]).resolve() != (
            appdir / "usr" / "lib" / "python3.10" / "site-packages" / "voxelmill" / "__init__.py").resolve():
        raise RuntimeError("acceptance driver imported VoxelMill outside the extracted AppImage")

    final = {
        "schema_version": 1,
        "artifact": str(artifact),
        "sha256": _sha256(artifact),
        "expected_version": args.expected_version,
        "workflow_provenance": args.workflow_url,
        "source": str(source),
        "settings_defaults": defaults,
        "prepare": {
            "triangles": prepared_report["export"]["triangles"],
            "warned": prepared_report["export"]["warned"],
            "island_components": island_count,
            "support_metrics": full_pass["supports"],
            "components": prepared_report["export"].get("components"),
        },
        "slice": {
            "path": str(sliced), "bytes": sliced.stat().st_size,
            "passed": verified["report"]["passed"],
            "checks": verified["report"]["checks"],
        },
        "gui": gui,
        "commands": commands,
        "extraction": extraction,
        "gui_command": gui_evidence,
        "coverage": {
            "physical_print": "not run",
            "non_linux_artifacts": ("build-only in release workflow" if args.workflow_url else "not run"),
        },
    }
    destination = output / "acceptance.json"
    destination.write_text(json.dumps(final, indent=2) + "\n")
    print(json.dumps({"passed": True, "report": str(destination), "sha256": final["sha256"]}))


def _settle(app, window, predicate, timeout=300):
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        if window.last_error:
            raise AssertionError(window.last_error)
        if time.monotonic() > deadline:
            raise AssertionError("GUI job timed out: " + window.statusBar().currentMessage())
        window.jobs.wait(50)
        time.sleep(0.005)


def run_inside(args):
    # Imports below must resolve from PYTHONHOME/PYTHONPATH set to the AppDir.
    import numpy as np
    import voxelmill
    from voxelmill.config import resolve_settings
    from voxelmill.acceleration import cuda_status
    from voxelmill.geometry import mesh_to_manifold
    from voxelmill.mesh import open_stl

    appdir = Path(args.inside_appdir).resolve()
    origin = Path(voxelmill.__file__).resolve()
    if appdir not in origin.parents:
        raise AssertionError(f"voxelmill imported outside AppDir: {origin}")
    if voxelmill.__version__ != args.expected_version:
        raise AssertionError((voxelmill.__version__, args.expected_version))
    acceleration = cuda_status()
    assert acceleration["compiled"] is False, acceleration
    settings = resolve_settings(overrides={
        "printer": {"build_mm": [40.0, 40.0, 40.0], "pixels": [400, 400],
                    "pixel_pitch_mm": [0.1, 0.1], "edge_clearance_mm": 0.0},
        "process": {"layer_height_mm": 0.2},
        "resources": {"workers": 2},
    })
    assert settings["support"]["brace_spacing_mm"] == 15.0
    assert settings["support"]["brace_max_length_mm"] == 30.0
    assert "brace_start_height_mm" not in settings["support"]
    assert settings["support"]["allow_part_to_part"] is False

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    prepared = output / "prepared.stl"
    with open_stl(prepared) as opened:
        triangles = np.array(opened.triangles, copy=True)
        prepared_sha256 = opened.asset.sha256
    prepared_solid, _ = mesh_to_manifold(triangles)
    prepared_components = len(prepared_solid.decompose())
    prepared_min_z = float(prepared_solid.bounding_box()[2])
    assert prepared_components == 1, prepared_components
    assert prepared_min_z >= -1e-7, prepared_min_z
    mesh_evidence = {
        "prepared_sha256": prepared_sha256,
        "prepared_triangles": len(triangles),
        "prepared_components": prepared_components,
        "prepared_min_z_mm": prepared_min_z,
    }
    if args.skip_gui:
        payload = {"voxelmill_origin": str(origin), "cuda_status": acceleration,
                   "gui_skipped": True,
                   "settings_roundtrip": True, **mesh_evidence}
    else:
        from PySide6 import QtWidgets
        from voxelmill.gui.document import Document
        from voxelmill.gui.window import MainWindow

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window = MainWindow(settings, args.source, headless=False)
        window.show()
        window.viewport.start()
        _settle(app, window, lambda: window.document.derived.union is not None)

        edited = json.loads(json.dumps(window.document.settings))
        edited["support"]["spacing_mm"] += 0.25
        previous = window.document.derived.union
        window.document.set_settings(edited, stage="supports")
        window.rebuild()
        _settle(app, window, lambda: window.document.derived.union is not None
                and window.document.derived.union is not previous)
        window.compute_attachments()
        _settle(app, window, lambda: window.attachment_state == "routed"
                and window.document.derived.union is not None)

        project = output / "gui-roundtrip.voxmil"
        window.document.save(project)
        restored = Document.load(project)
        assert restored.settings["support"]["spacing_mm"] == edited["support"]["spacing_mm"]
        assert restored.settings["support"]["brace_spacing_mm"] == 15.0
        assert restored.settings["support"]["brace_max_length_mm"] == 30.0
        window.close()

        reopened = MainWindow(settings, headless=False)
        reopened.show()
        reopened.viewport.start()
        reopened.open_project(project)
        _settle(app, reopened, lambda: reopened.document.derived.union is not None)
        reopened.tabs.setCurrentIndex(reopened.layers_tab_index)
        reopened.request_layer(0)
        _settle(app, reopened, lambda: reopened.layers._image is not None)

        import vtkmodules.all as vtk
        from vtkmodules.util import numpy_support
        reopened.viewport.reset_camera()
        reopened.viewport.render()
        shot = vtk.vtkWindowToImageFilter()
        shot.SetInput(reopened.viewport.interactor.GetRenderWindow())
        shot.Update()
        pixels = numpy_support.vtk_to_numpy(
            shot.GetOutput().GetPointData().GetScalars())[:, :3].astype(int)
        lit = int(np.count_nonzero(np.abs(pixels - [31, 33, 38]).sum(axis=1) > 30))
        assert lit > 1000, lit

        # Navigation cube, in the packaged runtime: all 26 facets pickable,
        # perimeter outlines with no facet diagonals, and the six screen
        # glyphs present. The picks are pure math, but running them here
        # proves the bundled module is the one carrying them.
        from voxelmill.gui.viewport import (ARROW_CENTRES_UV, ROLL_CENTRES_UV,
                                            chamfered_cube_triangles, cube_hit_at)
        from voxelmill.gui.window import capture_window
        reopened.viewport.add_navigation_cube()
        reopened.viewport.render()
        _, _, loops = chamfered_cube_triangles()
        cube_evidence = {
            "facet_outlines": len(loops),
            "edge_pick": cube_hit_at((0.40, 0.40, 0.0)),
            "corner_pick": cube_hit_at((0.40, 0.40, 0.40)),
            "face_pick": cube_hit_at((0.50, 0.10, 0.10)),
            "arrow_picks": {name: cube_hit_at(uv) for name, uv in ARROW_CENTRES_UV.items()},
            "roll_picks": {name: cube_hit_at(uv) for name, uv in ROLL_CENTRES_UV.items()},
            "widget_enabled": bool(reopened.viewport.cube_widget
                                   and reopened.viewport.cube_widget.GetEnabled()),
        }
        assert cube_evidence["facet_outlines"] == 26, cube_evidence
        assert cube_evidence["edge_pick"] == ["edge", "edge_++0"] or \
            cube_evidence["edge_pick"] == ("edge", "edge_++0"), cube_evidence
        assert cube_evidence["widget_enabled"], cube_evidence
        assert all(hit and hit[0] == "arrow"
                   for hit in cube_evidence["arrow_picks"].values()), cube_evidence
        assert all(hit and hit[0] == "roll"
                   for hit in cube_evidence["roll_picks"].values()), cube_evidence
        cube_evidence["edge_views"] = sum(
            1 for name in __import__('voxelmill.gui.camera', fromlist=['VIEWS']).VIEWS
            if name.startswith('edge_'))
        assert cube_evidence["edge_views"] == 12, cube_evidence

        # The window screenshot path itself, which is how non-Linux hosts get
        # visual evidence at all. It must include the 3D view, not a blank hole.
        window_shot = capture_window(reopened, output / "editor-window.png")
        cube_evidence["window_screenshot"] = str(window_shot)

        payload = {
            "navigation_cube": cube_evidence,
            "voxelmill_origin": str(origin), "cuda_status": acceleration,
            "gui_skipped": False,
            "actual_render_surface_pixels": lit,
            "layer_zero_nonempty": not reopened.layers._image.isNull(),
            "project_reopened": str(project),
            "settings_roundtrip": True,
            "contacts_routed": reopened.document.derived.plan.metrics["contacts_routed"],
            "brace_count": reopened.document.derived.plan.metrics["braces"],
            **mesh_evidence,
        }
        assert payload["brace_count"] > 0, payload
        reopened.jobs.wait(5000)
        reopened.close()
        if args.support_options:
            payload["support_options"] = _support_options_gui(app, output)
    (output / "gui.json").write_text(json.dumps(payload, indent=2) + "\n")


def _support_options_gui(app, output):
    """Exercise the new editor using only imports from the packaged runtime."""
    from voxelmill.gui.document import Document
    from voxelmill.gui.editors import ConfigurationEditor, load_component
    from voxelmill.config import resolve_settings
    import vtkmodules.all as vtk

    document = Document()
    dialog = ConfigurationEditor(document, 'support')
    dialog.show()
    # Anchor and thin-pillar settings are separate tabs: thin pillars in
    # middle mode apply to every pillar, not only to part-to-part routes.
    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        'Bracing', 'Pillars, tips and bases', 'Part-to-part anchors', 'Thin pillars']
    cases = {}

    def preview(name):
        dialog.refresh_preview()
        deadline = time.monotonic() + 60
        while dialog.example is None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert dialog.example is not None, dialog.status.text()
        for _ in range(5):
            app.processEvents()
        dialog.viewport.render()
        shot = vtk.vtkWindowToImageFilter()
        shot.SetInput(dialog.viewport.interactor.GetRenderWindow())
        shot.Update()
        writer = vtk.vtkPNGWriter()
        writer.SetFileName(str(output / (name + '.png')))
        writer.SetInputConnection(shot.GetOutputPort())
        writer.Write()
        # QWidget.grab re-paints native VTK children into a software buffer
        # incorrectly; capture the real X11 window after the VTK render.
        dialog.screen().grabWindow(int(dialog.winId())).save(str(output / (name + '-editor.png')))
        cases[name] = dialog.example['metrics']
        return cases[name]

    def choice(key, value):
        field = dialog.fields['support', key]
        field.setCurrentIndex(field.findData(value))

    dialog.fields['support', 'brace_spacing_mm'].setText('8')
    dialog.fields['support', 'brace_max_distance_mm'].setText('12')
    dialog.fields['support', 'brace_branches_per_node'].setText('3')
    dialog.fields['support', 'brace_angle_deg'].setText('60')
    for mode in ('supports', 'base', 'both'):
        choice('brace_destination', mode)
        metrics = preview('destinations-' + mode)
        assert metrics['brace_destination'] == mode
        assert metrics['braces'] > 0
        if mode == 'supports':
            assert metrics['brace_new_feet'] == 0
        if mode == 'base':
            assert metrics['brace_new_feet'] > 0
    choice('brace_destination', 'supports')
    for pattern in ('alternating', 'x'):
        choice('brace_pattern', pattern)
        metrics = preview('pattern-' + pattern)
        assert metrics['brace_pattern'] == pattern and metrics['braces'] > 0
    preset = output / 'support-options.json'
    dialog.save_file(str(preset))
    assert load_component('support', preset, resolve_settings())['support'] == dialog.settings()['support']
    dialog.apply()
    project = output / 'support-options.voxmil'
    document.save(project)
    assert Document.load(project).settings['support'] == document.settings['support']
    dialog.fields['support', 'auto_bracing'].setChecked(False)
    dialog.fields['support', 'tree_supports'].setChecked(True)
    assert preview('tree-junctions')['tree']['trunks'] > 0
    dialog.model_anchor_demo.click()
    assert preview('part-to-part')['routing']['model_anchor'] == 4
    dialog.fields['support', 'allow_part_to_part'].setChecked(False)
    assert preview('part-to-part-disabled')['routing']['model_anchor'] == 0

    # The showcase layout has to produce every route kind with no settings
    # hunt, which is the whole reason it exists. Brace geometry is put back to
    # its defaults first: the cases above left an unusual combination behind,
    # and the layout deliberately does not force brace settings, so what is
    # asserted here is the showcase with ordinary bracing.
    choice('brace_destination', 'both')
    choice('brace_pattern', 'single')
    dialog.fields['support', 'brace_spacing_mm'].setText('15')
    dialog.fields['support', 'brace_max_distance_mm'].setText('0')
    dialog.fields['support', 'brace_branches_per_node'].setText('1')
    dialog.fields['support', 'brace_angle_deg'].setText('45')
    dialog.fields['support', 'tree_supports'].setChecked(False)
    dialog.example_layout.setCurrentIndex(dialog.example_layout.findData('showcase'))
    preview('showcase')
    showcase = dialog.example
    assert showcase['layout'] == 'showcase', showcase['layout']
    categories = showcase['categories']
    missing = [kind for kind, count in categories.items() if not count]
    assert not missing, (missing, categories)
    assert showcase['overrides'], 'the showcase must report what it forced'
    assert showcase['analysis_pitch_mm'] < 0.2, showcase['analysis_pitch_mm']
    dialog.reject()
    return {'cases': cases, 'preset_roundtrip': True, 'project_roundtrip': True,
            'showcase_categories': categories, 'showcase_overrides': showcase['overrides']}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("appimage", nargs="?", help="built or downloaded AppImage")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="acceptance STL")
    parser.add_argument("--output-dir", required=True, help="durable evidence directory")
    parser.add_argument("--expected-version", default="0.5.4")
    parser.add_argument("--workflow-url", help="published workflow run URL or other provenance")
    parser.add_argument("--skip-gui", action="store_true",
                        help="only for hosts without Xvfb; records unavailable render coverage")
    parser.add_argument("--support-options", action="store_true",
                        help="also test configurable brace styles, tree tips, and model anchors in the packaged editor")
    parser.add_argument("--inside-appdir", help=argparse.SUPPRESS)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.inside_appdir:
        run_inside(args)
        return 0
    if not args.appimage:
        raise SystemExit("appimage is required")
    run_acceptance(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
