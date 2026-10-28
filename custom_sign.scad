// Custom sign - parametric OpenSCAD version
//
// Two-colour sign: a yellow base plate with a raised black border, an icon
// silhouette and text (two-colour print via Z pause / filament swap, or
// export base and raised parts separately for an AMS/MMU).
//
// Edit the parameters below (or use Window > Customizer in OpenSCAD).
// To change the icon: trace any black-on-white image to an SVG with
//   ./.venv/bin/python trace_silhouette.py image.png icon.svg
// (see that script's --help for crop/threshold options) and re-render.
// Export:  part = "both"   -> one combined STL
//          part = "base"   -> yellow plate only
//          part = "raised" -> black border + icon + text only
// Command line example:
//   openscad -D 'part="base"'   -o custom_sign_base.stl   custom_sign.scad
//   openscad -D 'part="raised"' -o custom_sign_raised.stl custom_sign.scad

/* [Text] */
// One entry per line, top to bottom. Add or remove lines freely.
lines = ["I CAN CLEAR THE FENCE", "IN 0.6 SECONDS. CAN YOU?"];
// Font (fontconfig name). Run `fc-list` to see what's installed.
font = "Nimbus Sans Narrow:style=Bold";
// Text size. 10.35 makes the longest default line just fill the width.
text_size = 10.35;
// Gap between text lines
line_gap = 3.0;
// Cap height / text_size for the chosen font (1.024 measured for Nimbus Sans Narrow Bold). Only affects layout.
cap_ratio = 1.024;

/* [Icon] */
// SVG silhouette file, relative to this .scad. Make one with trace_silhouette.py
icon_file = "icon.svg";
// Show the icon above the text
show_icon = true;
// Icon height
icon_h = 30.0;
// Gap between icon and first text line
gap_icon_text = 3.0;
// Mirror the icon horizontally
icon_flip = false;

/* [Plate] */
// Overall width. Height is worked out from the content.
width = 180.0;
// Outer corner radius
corner_r = 10.8;
// Base plate thickness (yellow)
base_t = 3.0;
// Height of the raised black features above the plate
raise_t = 1.0;
// Border ring: distance from plate edge to ring
ring_inset = 4.2;
// Border ring width
ring_w = 3.7;
// Clearance between the ring and the content
inner_pad = 4.0;

/* [Mounting holes] */
// Hole X positions (list). Use [] for no holes.
hole_x = [24, 156];
// Where the hole rows go. "top" sits beside the icon (or in a band above the text if there is no icon); "bottom" adds a band below the text. Use both for tall signs.
hole_rows = "top"; // [top, bottom, top+bottom]
// Through hole diameter (4.2 = M4 / #8 screw clearance)
hole_d = 4.2;
// Countersink diameter at the top face (0 = no countersink)
csk_d = 8.2;

/* [Export] */
// Which part to generate
part = "both"; // [both, base, raised]
// Curve resolution
$fn = 64;

/* [Hidden] */
n_lines = len(lines);
cap_h = text_size * cap_ratio;
text_block_h = n_lines * cap_h + max(0, n_lines - 1) * line_gap;
has_holes = len(hole_x) > 0;
want_top = has_holes && (hole_rows == "top" || hole_rows == "top+bottom");
want_bot = has_holes && (hole_rows == "bottom" || hole_rows == "top+bottom");
band_h = max(hole_d, csk_d) + 2;                  // height reserved for a hole row
// Top holes sit beside the icon when there is one, so they only need a band of their own without it.
top_band = (want_top && !show_icon) ? band_h + gap_icon_text : 0;
bot_band = want_bot ? band_h + line_gap : 0;
content_h = top_band + (show_icon ? icon_h + gap_icon_text : 0) + text_block_h + bot_band;
margin = ring_inset + ring_w + inner_pad;      // plate edge -> content
height = content_h + 2 * margin;
inner_w = width - 2 * margin;
content_top = height - margin;
icon_y = content_top - top_band - icon_h;         // bottom of the icon
text_top = show_icon ? icon_y - gap_icon_text : content_top - top_band;   // top of the first text line
hole_ys = concat(
    want_top ? [show_icon ? content_top - icon_h / 2 : content_top - band_h / 2] : [],
    want_bot ? [margin + band_h / 2] : []);
eps = 0.01;

echo(str("plate ", width, " x ", height, " mm, inner width ", inner_w, " mm"));

// ------------------------------------------------------------------
// Icon from the SVG, scaled to h tall and centred on the origin.
module icon_2d(h = icon_h) {
    resize([0, h], auto = true) import(icon_file, center = true);
}

// ------------------------------------------------------------------
module rrect(w, h, r) {
    offset(r = r) offset(delta = -r) square([w, h]);
}

module plate_2d() {
    difference() {
        rrect(width, height, corner_r);
        for (x = hole_x, y = hole_ys) translate([x, y]) circle(d = hole_d);
    }
}

module ring_2d() {
    difference() {
        translate([ring_inset, ring_inset])
            rrect(width - 2 * ring_inset, height - 2 * ring_inset, corner_r - ring_inset);
        translate([ring_inset + ring_w, ring_inset + ring_w])
            rrect(width - 2 * (ring_inset + ring_w), height - 2 * (ring_inset + ring_w),
                  corner_r - ring_inset - ring_w);
    }
}

module text_2d() {
    for (i = [0 : n_lines - 1]) {
        baseline = text_top - cap_h - i * (cap_h + line_gap);
        translate([width / 2, baseline])
            text(lines[i], size = text_size, font = font, halign = "center", valign = "baseline");
    }
}

module raised_2d() {
    ring_2d();
    if (show_icon)
        translate([width / 2, icon_y + icon_h / 2])
            mirror([icon_flip ? 1 : 0, 0]) icon_2d();
    text_2d();
}

module base() {
    color([1, 0.82, 0.01]) difference() {
        linear_extrude(base_t) plate_2d();
        if (csk_d > hole_d)
            for (x = hole_x, y = hole_ys)
                translate([x, y, base_t - (csk_d - hole_d) / 2])
                    cylinder(d1 = hole_d, d2 = csk_d + 2 * eps, h = (csk_d - hole_d) / 2 + eps);
    }
}

module raised() {
    color([0.08, 0.08, 0.08])
        translate([0, 0, base_t - eps]) linear_extrude(raise_t + eps) raised_2d();
}

if (part == "base" || part == "both") base();
if (part == "raised" || part == "both") raised();
