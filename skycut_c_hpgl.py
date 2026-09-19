#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_c_hpgl.py

Turns flattened cut/score/draw point lists into an HPGL command stream
for the SkyCut cutter, including per-tool speed/pressure setup and the
ContourCut ("TB"/"CT1") registration-mark preamble.

SkyCut uses a different coordinate convention from normal SVG/HPGL:
SVG:
    X right, Y down

SkyCut HPGL:
    axes are swapped and inverted.

Conversion:
    hpgl_x = max_y - y
    hpgl_y = max_x - x

Settings (speed/pressure/offset/overcut) live per PHYSICAL TOOL rather
than per operation - each tool is independently assigned to work the
Full Cut layer, the Kiss Cut layer, the Crease layer, the Draw layer,
or nothing (None). A "side" here is a dict with keys:
    side      - a name for this physical tool, e.g. "left"/"right" (two-
                tool machines) or "tool" (single-tool machines)
    kind      - "cut" | "score" | "draw" | "crease" | "none"
    paths     - list of (pts, kind) tuples, already run through
                build_cut_path with this tool's offset/overcut
    up_speed, speed, pressure, offset - this tool's settings

Emission order is simply the order `sides` is passed in - callers
with more than one tool (e.g. Operation Mode's Left/Right ordering)
decide that order themselves before calling in; a tool whose kind is
"none" is always skipped regardless of position.
"""

from skycut_c_constants import SCALE


def emit_polyline(hpgl, pts, max_x, max_y, scale=SCALE):
    """
    Append U/D HPGL commands for one polyline.

    Applies SkyCut coordinate conversion.

    pts have already been through build_cut_path() (see
    skycut_c_pathbuild.py), which is where knife/score offset actually
    gets applied - as direction-aware overshoot/return excursions at
    each corner, matching a swiveling drag-knife's real behavior
    (blade trails behind the pivot along the current direction of
    travel). On straight runs between corners that trailing model
    needs no correction at all, so no offset is applied here - doing
    so a second time distorted the corner geometry that build_cut_path
    already places precisely.
    """
    for i, (x, y) in enumerate(pts):
        hpgl_x = max_y - y
        hpgl_y = max_x - x

        hpgl.append(
            ("U" if i == 0 else "D") +
            f"{int(round(hpgl_x * scale))},{int(round(hpgl_y * scale))};"
        )


def _tool_command(side):
    """
    SkyCut tool-select command: P0 selects the primary/left tool head,
    P1 selects the right tool head. Selected per-block (each tool can
    be doing different work), rather than once globally - so each
    tool's settings actually take effect independently. Single-tool
    setups (side name anything other than "right") always get P0.
    """
    return "P0;" if side != "right" else "P1;"


def _rounded_points_natural(pts, scale=SCALE):
    """
    Same HPGL-unit rounding as the real output, but WITHOUT the
    SkyCut axis-swap conversion.

    The physical cutter needs the swap (SkyCut's X/Y differ from
    other cutters), but that's a machine-orientation detail, not
    something the person looking at the viewer needs to mentally
    undo - the viewer shows the artwork in its natural Inkscape
    orientation instead. Corner overshoots, overcuts, offset (already
    baked into pts via build_cut_path), and rounding all still match
    the real output exactly - only the final swap step (a display/
    machine-orientation concern, not a geometry one) is skipped here.
    """
    out = []
    for x, y in pts:
        out.append((
            round(x * scale) / scale,
            round(y * scale) / scale,
        ))
    return out


def _emit_side_block(hpgl, side_data, max_x, max_y, scale=SCALE):
    hpgl.append(_tool_command(side_data["side"]))
    hpgl.extend([
        f"US{side_data['up_speed']};",
        f"VS{side_data['speed']};",
        f"!FS{side_data['pressure']};",
    ])
    for pts, _kind in side_data["paths"]:
        emit_polyline(hpgl, pts, max_x, max_y, scale)


def build_render_segments(sides, options, scale=SCALE):
    """
    Build viewer geometry in natural (unrotated) Inkscape orientation.

    The real HPGL output (assemble_hpgl/emit_polyline) still applies
    SkyCut's axis-swap conversion, since the physical cutter needs
    it. The viewer intentionally does NOT apply that swap - it shows
    the artwork the way it looks in Inkscape, so what's on screen is
    recognizable at a glance, while offsets/overcuts/corner handling
    still match the real output exactly.

    Emits in the order `sides` is given - a tool with kind "none" is
    always skipped.
    """
    segments = []
    for s in sides:
        if s["kind"] == "none":
            continue
        for pts, _k in s["paths"]:
            segments.append({
                "kind": s["kind"],
                "side": s["side"],
                "points": _rounded_points_natural(pts, scale),
            })
    return segments


def assemble_hpgl(sides, options, min_x, min_y, max_x, max_y, scale=SCALE):
    """
    Build complete HPGL stream for SkyCut.

    `sides` is the list of per-tool dicts described at the top of
    this module (one entry per physical side that has geometry to
    emit).
    """

    hpgl = ["IN;"]

    if options.cut_mode == "contourcut":
        width_units = int(round((max_x - min_x) * scale))
        height_units = int(round((max_y - min_y) * scale))

        hpgl.append(
            f"TB25,{height_units},{width_units};"
        )
        hpgl.append("CT1;")

    hpgl.append("PA;")

    for s in sides:
        if s["kind"] == "none":
            continue
        _emit_side_block(hpgl, s, max_x, max_y, scale)

    hpgl.extend([
        "U0,0;",
        "@;"
    ])

    if options.cut_mode == "contourcut":
        hpgl.append("@;")

    return "\n".join(hpgl)
