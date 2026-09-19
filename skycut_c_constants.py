#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
skycut_c_constants.py

Master switches and shared numeric constants for the SkyCut Command
extension (CUT + SCORE + CONTOUR, Model C).

Kept in one place so every module (and the main effect script) agrees
on the same values instead of redefining them.
"""

import math

# Debug / capture only - never exposed in the INX UI.
CAPTURE_ENABLE_MASTER = False

# HPGL units-per-mm scale factor.
SCALE = 40

# Bezier flattening tolerance, in mm.
FLATTEN_TOLERANCE_MM = 0.005

# Stroke width used for debug/preview capture layers, in mm.
CAPTURE_STROKE_WIDTH = 0.05

# Corners sharper... err, wider than this angle are ignored for
# knife-offset overshoot/return generation.
MIN_CORNER_ANGLE = math.radians(15)

# Fraction of the knife offset used for the "return" leg of a corner.
RETURN_TRIM = 1.0

# ----------------------------------------------------------------
# Preview colors - fixed (not user-configurable) per-kind colors for
# the Developer Use preview layer and the Preview Path viewer only.
# No effect on the physical cutter. Edit the hex values here to
# change what shows up in both places.
#
# Kind names map to real-world vinyl/sign terms: "cut" = Full Cut,
# "score" = Kiss Cut.
PREVIEW_COLORS = {
    "cut":    "#FF00FF",  # Full Cut
    "score":  "#FF0000",  # Kiss Cut
    "draw":   "#00B050",  # Draw
    "crease": "#00B7EB",  # Crease
    "marks":  "#FFFF00",  # Contour Cut registration marks
    "print":  "#0038A8",  # Print layer (Layer Setup only)
}

# Display names matching the Layer Selection option labels in the
# INX GUI - used for preview-layer/legend text so wording stays
# consistent with what's shown in Left/Right Tool.
LAYER_DISPLAY_NAMES = {
    "cut": "Full Cut",
    "score": "Kiss Cut",
    "crease": "Crease Tool",
    "draw": "Draw",
}

# Actual SVG layer names collect_layer() searches the document for.
# These match what skycut_c_layer_setup.py creates - NOT always the
# same text as LAYER_DISPLAY_NAMES above (the Crease *tool* is
# described as "Crease Tool" in the dropdown, but the layer itself is
# just named "Crease").
SOURCE_LAYER_NAMES = {
    "cut": "Full Cut",
    "score": "Kiss Cut",
    "crease": "Crease",
    "draw": "Draw",
}
