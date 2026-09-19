#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Skycut Command Model C v4 (single-tool)
#
# This is the Inkscape entry point (the script an .inx file's
# <command> points to). All heavy lifting lives in the sibling
# skycut_c_*.py modules in this same directory - this is a fully
# standalone extension, not a variant that shares files with
# skycut_command_d24_1.py (the two-tool machine); every module below
# is Model C's own copy:
#
#   skycut_c_constants.py  - shared master switches / numeric constants
#   skycut_c_geometry.py   - pure vector math + Bezier flattening
#   skycut_c_arrows.py     - debug direction-arrow polylines
#   skycut_c_pathbuild.py  - knife-offset corner overshoot/return/overcut
#   skycut_c_reference.py  - wysiwyg/origin/contourcut reference-box logic
#   skycut_c_layers.py     - SVG layer management + geometry collection (mixin)
#   skycut_c_hpgl.py        - HPGL command assembly
#   skycut_c_output.py      - save-to-file / send-to-cutter delivery
#   skycut_c_usb.py         - Windows USB-Printer device discovery + raw send,
#                              used by skycut_c_output.py for Send to Machine USB
#   skycut_c_viewer.py      - Preview Path HTML viewer
#   skycut_c_layer_setup.py - Action: Layer Setup
#   skycut_c_add_marks.py   - Action: Add Marks
#
# TOOL MODEL
# ----------
# Model C has one physical tool head and no tool-changing routine -
# whatever's physically loaded (blade / crease tool / pen) is up to
# the person, and tool_layer (a single param) says which layer(s) get
# sent this run. Which settings block applies is picked automatically
# from that same value:
#   cut / score / cut_score -> Blade Tool Settings
#   crease / draw           -> Pen / Crease Tool Settings (shared -
#                               crease and draw are both light,
#                               offset-free passes so one settings
#                               block covers either)
#
# tool_layer="cut_score" ("Full Cut & Kiss Cut") is a same-blade
# combo run: Kiss Cut is cut first (Blade's Kiss Cut Pressure), then
# Full Cut is appended to the same job (Blade's Full Cut Pressure) -
# one HPGL stream, one "IN;" at the top, no re-init between the two.
#
# "Draw" and "Crease" are pen/blade-free passes - they reuse the same
# speed/pressure machinery as cut and score, but knife-offset
# compensation doesn't make physical sense for them, so effect()
# warns (without blocking the job) if Tool is assigned to either with
# a nonzero offset.

import inkex

from skycut_c_layers import SkyCutLayerMixin
from skycut_c_pathbuild import build_cut_path
from skycut_c_reference import compute_reference_box, get_contour_mark_paths, ReferenceError
from skycut_c_hpgl import assemble_hpgl, build_render_segments
from skycut_c_output import save_hpgl_file, send_hpgl, send_hpgl_usb
from skycut_c_viewer import open_hpgl_viewer
from skycut_c_layer_setup import run_layer_setup
from skycut_c_add_marks import run_add_marks
from skycut_c_constants import SOURCE_LAYER_NAMES, LAYER_DISPLAY_NAMES

# Actual SVG layer names to search the document for, per kind - see
# SOURCE_LAYER_NAMES in skycut_c_constants.py (also used by
# skycut_c_layer_setup.py, so the two stay in sync within this
# extension).
LAYER_LABELS = SOURCE_LAYER_NAMES


class SkyCutCommand_ModelC_V4(SkyCutLayerMixin, inkex.EffectExtension):

    # ------------------------------------------------------
    def add_arguments(self, p):
        # Notebook tab selector (Settings/Help) - value itself isn't
        # used by effect(), but Inkscape always passes it as a CLI
        # arg for any <param type="notebook">, so argparse needs to
        # know about it or every run fails with "unrecognized
        # arguments: --tab=...".
        p.add_argument("--tab", default="settings")
        # Blade Tool (Full Cut / Kiss Cut / Full Cut & Kiss Cut) - pressure
        # is split per-kind since kiss cutting normally needs much less
        # pressure than a full cut, even with the same physical blade.
        p.add_argument("--blade_tool_speed", type=int, default=5)
        p.add_argument("--blade_tool_up_speed", type=int, default=5)
        p.add_argument("--blade_tool_pressure_cut", type=int, default=5)
        p.add_argument("--blade_tool_pressure_score", type=int, default=5)
        p.add_argument("--blade_tool_offset_mm", type=float, default=0.30)
        p.add_argument("--blade_tool_overcut_mm", type=float, default=1.0)
        p.add_argument("--blade_tool_passes", type=int, default=1)
        # Crease Tool
        p.add_argument("--crease_tool_speed", type=int, default=5)
        p.add_argument("--crease_tool_up_speed", type=int, default=5)
        p.add_argument("--crease_tool_pressure", type=int, default=5)
        p.add_argument("--crease_tool_offset_mm", type=float, default=0.0)
        p.add_argument("--crease_tool_overcut_mm", type=float, default=1.0)
        p.add_argument("--crease_tool_passes", type=int, default=1)
        # Tool - one physical head, no tool-changing routine. This
        # single value picks which layer(s) get sent this run; the
        # matching settings block above (Blade/Crease/Pen) is applied
        # automatically based on what's picked.
        p.add_argument("--tool_layer", default="cut")
        # Modes
        p.add_argument("--cut_mode", default="origin")
        # Developer Use
        p.add_argument("--preview_enable", type=inkex.Boolean, default=False)
        # Marks Setting
        p.add_argument("--mark_mode", default="objects")
        p.add_argument("--mark_distance_mm", type=float, default=5.0)
        p.add_argument("--mark_arm_mm", type=float, default=10.0)
        p.add_argument("--mark_stroke_mm", type=float, default=1.0)
        # Action
        p.add_argument("--output_mode", default="send")
        p.add_argument("--ip", default="192.168.16.200")
        p.add_argument("--port", type=int, default=8080)
        p.add_argument("--usb_device_path", default="")

    # ------------------------------------------------------
    def _tool_profile(self, kind):
        """
        Return the speed/up_speed/pressure/offset/overcut/passes dict
        for whichever physical tool `kind` belongs to - Blade (cut,
        score), Crease, or Pen (draw). Full Cut and Kiss Cut are the
        same blade with different pressure presets.
        """
        o = self.options
        if kind == "cut":
            return dict(speed=o.blade_tool_speed, up_speed=o.blade_tool_up_speed,
                        pressure=o.blade_tool_pressure_cut, offset=o.blade_tool_offset_mm,
                        overcut=o.blade_tool_overcut_mm, passes=o.blade_tool_passes)
        if kind == "score":
            return dict(speed=o.blade_tool_speed, up_speed=o.blade_tool_up_speed,
                        pressure=o.blade_tool_pressure_score, offset=o.blade_tool_offset_mm,
                        overcut=o.blade_tool_overcut_mm, passes=o.blade_tool_passes)
        if kind == "crease" or kind == "draw":
            return dict(speed=o.crease_tool_speed, up_speed=o.crease_tool_up_speed,
                        pressure=o.crease_tool_pressure, offset=o.crease_tool_offset_mm,
                        overcut=o.crease_tool_overcut_mm, passes=o.crease_tool_passes)
        raise ValueError(f"Unknown tool kind: {kind}")

    # ======================================================
    def effect(self):
        # "Layer Setup" and "Add Marks" each run their own module
        # only, skipping the entire cut/score/draw/crease pipeline
        # below.
        if self.options.output_mode == "layer_setup":
            run_layer_setup(self.svg)
            return

        if self.options.output_mode == "add_marks":
            run_add_marks(self.svg, self.options.mark_mode,
                           self.options.mark_distance_mm,
                           self.options.mark_arm_mm,
                           self.options.mark_stroke_mm)
            return

        if self.options.tool_layer == "none":
            inkex.errormsg("Tool is set to None; aborting.")
            return

        # "Full Cut & Kiss Cut" is a same-blade combo: Kiss Cut runs
        # first, Full Cut is appended after, in that order, in the
        # same job. Anything else is just that one kind on its own.
        if self.options.tool_layer == "cut_score":
            kinds_in_order = ["score", "cut"]
        else:
            kinds_in_order = [self.options.tool_layer]

        side_specs = [
            {"side": "tool", "kind": kind, **self._tool_profile(kind)}
            for kind in kinds_in_order
        ]

        # Rect/circle/ellipse/etc drawn with Inkscape's shape tools
        # aren't <svg:path> elements, so collect_layer() below would
        # silently skip them - they'd just never get cut/scored, with
        # no visual difference on the canvas to explain why. Checked
        # across ALL FOUR source layers every run (not just whichever
        # one Tool is currently assigned to) so a stray shape sitting
        # in an unused layer gets caught now rather than surfacing as
        # a surprise the next time someone runs that layer. Convert
        # them to real paths automatically (the same result as Path >
        # Object to Path), then stop so the person can check the
        # result before anything gets sent - do NOT proceed straight
        # into cutting off an automatic conversion within the same run.
        offenders = self.find_unconverted_shapes(list(LAYER_LABELS.values()))
        if offenders:
            by_layer = {}
            for el, source_layer in offenders:
                by_layer.setdefault(source_layer, []).append(el)

            breakdown = "\n".join(
                f"  {source_layer}: {len(els)} shape(s) "
                f"({', '.join(sorted({e.tag.split('}')[-1] for e in els}))})"
                for source_layer, els in by_layer.items()
            )

            self.convert_shapes_to_paths([el for el, _ in offenders])

            inkex.errormsg(
                "Found shapes that weren't paths yet, so they would have been "
                "skipped when cutting:\n\n"
                f"{breakdown}\n\n"
                "They've been converted to paths now. Check the result on the "
                "canvas - if it looks right, run this again to send the job."
            )
            return

        # Draw and Crease are pen/blade-free passes (no knife-offset
        # compensation makes physical sense) - warn but keep going;
        # this isn't fatal the way a missing layer or reference box is.
        for spec in side_specs:
            if spec["kind"] in ("draw", "crease") and spec["offset"] != 0:
                dup = LAYER_DISPLAY_NAMES.get(spec["kind"], spec["kind"].title())
                inkex.errormsg(
                    f"Warning: Tool is assigned to {dup} but its offset is "
                    f"{spec['offset']}mm (not 0). Offset should normally be "
                    "0 for Draw/Crease - continuing anyway."
                )

        any_geometry = False
        for spec in side_specs:
            spec["raw_paths"] = self.collect_layer(LAYER_LABELS[spec["kind"]], spec["kind"])
            any_geometry = any_geometry or bool(spec["raw_paths"])

        if not any_geometry:
            lines = []
            for spec in side_specs:
                layer_name = LAYER_LABELS[spec["kind"]]
                if self.layer_exists(layer_name):
                    lines.append(f'  "{layer_name}" - layer exists, but has no cuttable path geometry in it')
                else:
                    lines.append(f'  "{layer_name}" - no layer with this name found in the document')
            inkex.errormsg(
                "No geometry found for the current Tool selection; aborting.\n\n"
                + "\n".join(lines) +
                "\n\nLayer names are matched case-insensitively, but must otherwise "
                "match exactly. If a layer is missing, run Action \u2192 Layer Setup "
                "to create the standard set, or check for a typo/rename. If a layer "
                "exists but is empty, make sure your artwork was actually drawn "
                "inside it (and not just visually overlapping it from another layer)."
            )
            return

        # Combined across every active side, since e.g. "origin" mode's
        # bounding box should reflect everything actually being sent
        # this run, not just one of the two combo layers.
        all_raw_paths = [p for spec in side_specs for p in spec["raw_paths"]]

        # Bounding reference box (wysiwyg / origin / contourcut) - see
        # skycut_c_reference.py for the coordinate-system explanation.
        try:
            min_x, min_y, max_x, max_y = compute_reference_box(
                self.svg, all_raw_paths, self.options.cut_mode
            )
        except ReferenceError as e:
            inkex.errormsg(str(e))
            return

        # Debug capture before overshoot (no-ops unless CAPTURE_ENABLE_MASTER)
        debug = self.new_layer("Debug")
        if debug is not None:
            self.capture(all_raw_paths, "Capture After Flatten", parent=debug)
            self.capture(all_raw_paths, "Capture After Dedup", parent=debug)
            self.capture(all_raw_paths, "Capture After Clean", parent=debug)
            self.capture(all_raw_paths, "Capture Before Force CW", parent=debug)
            self.capture(all_raw_paths, "Capture After Force CW", parent=debug)

        # Overshoot / return / overcut, then Passes - each object is
        # repeated `passes` times (pen lifts and returns to that
        # object's start) before the tool moves on to the next one.
        for spec in side_specs:
            built = [
                (build_cut_path(pts, spec["offset"], spec["overcut"]), kind)
                for pts, kind in spec["raw_paths"]
            ]
            passes = max(1, spec.get("passes", 1))
            spec["paths"] = [item for item in built for _ in range(passes)]

        if debug is not None:
            for spec in side_specs:
                label = LAYER_DISPLAY_NAMES.get(spec["kind"], spec["kind"].title())
                self.capture(spec["paths"],
                             f"Capture After Overshoot_Returns_Overcuts - {label}",
                             parent=debug)

        # Developer Use preview layer
        self.preview_paths(side_specs)

        if self.options.preview_enable:
            return

        output_mode = self.options.output_mode

        # Preview Path / Preview Path in Web Browser are QC-only: show
        # them, but don't also save or send. Unlike the real HPGL
        # output, the viewer intentionally shows the artwork in
        # natural (unrotated) Inkscape orientation - SkyCut's axis
        # swap is a machine-orientation detail that only needs to
        # apply to what's actually sent to the cutter.
        if output_mode in ("preview_path", "preview_web"):
            segments = build_render_segments(side_specs, self.options)

            # The blue "origin" dot marks the reference-box corner
            # that machine (0,0) corresponds to for this mode - NOT
            # necessarily wherever the first cut point happens to
            # land (that's the green dot, and depends on shape sort
            # order). All three modes use bottom-right (max_x, max_y) -
            # this also matches the real HPGL swap formula
            # (hpgl_x = max_y - y, hpgl_y = max_x - x), which only
            # ever sends a reference box's bottom-right corner to
            # machine (0,0), regardless of mode.
            origin_point = (max_x, max_y)

            marks = None
            if self.options.cut_mode == "contourcut":
                try:
                    marks = list(get_contour_mark_paths(self.svg).values())
                except ReferenceError:
                    marks = None

            mode_labels = {
                "origin": "Origin",
                "wysiwyg": "WYSIWYG",
                "contourcut": "Contour Cut",
            }
            mode_label = mode_labels.get(self.options.cut_mode, self.options.cut_mode)

            tools = [("Tool", spec["kind"]) for spec in side_specs]

            tasks = [
                {
                    "label": LAYER_DISPLAY_NAMES.get(spec["kind"], spec["kind"].title()),
                    "side": spec["side"],
                    "side_label": spec["side"].title(),
                    "pressure": spec["pressure"],
                    "speed": spec["speed"],
                    "up_speed": spec["up_speed"],
                }
                for spec in side_specs
            ]

            open_hpgl_viewer(segments, page_bounds=(min_x, min_y, max_x, max_y),
                              origin_point=origin_point, marks=marks,
                              mode_label=mode_label,
                              page_size=(max_x - min_x, max_y - min_y),
                              tools=tools, tasks=tasks,
                              force_browser_tab=(output_mode == "preview_web"))
            return

        # HPGL emission
        data = assemble_hpgl(side_specs, self.options, min_x, min_y, max_x, max_y)

        if output_mode == "save_plt":
            saved_path = save_hpgl_file(data, self.options.cut_mode)
            inkex.errormsg(f"PLT file saved successfully:\n{saved_path}")
        elif output_mode == "send_usb":
            success, message = send_hpgl_usb(data, self.options.usb_device_path)
            if not success:
                inkex.errormsg(f"Send failed:\n{message}")
        else:  # "send" (WiFi)
            success, message = send_hpgl(data, self.options.ip, self.options.port)
            if not success:
                inkex.errormsg(f"Send failed:\n{message}")


# ==========================================================
# RUN
# ==========================================================
if __name__ == "__main__":
    SkyCutCommand_ModelC_V4().run()
