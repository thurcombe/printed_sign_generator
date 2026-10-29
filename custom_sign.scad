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
// "rectangle": width is set, height follows the content. "round": a disc of the given diameter with the content centred (hole_x is ignored; holes go top and bottom centre, or in side tabs).
shape = "rectangle"; // [rectangle, round]
// Overall width of a rectangle. Height is worked out from the content.
width = 180.0;
// Diameter of a round sign. Must be big enough for the content; the web UI works this out, here check the preview.
diameter = 180.0;
// Outer corner radius of a rectangle
corner_r = 10.8;
// "raised": the border, icon and text stand proud of the plate. "flush": they are inlaid into the plate so the face is flat, for a sign that sits against a window (needs a multi-material print; export base and raised separately).
style = "raised"; // [raised, flush]
// Base plate thickness (yellow)
base_t = 3.0;
// Height of the black features above the plate (raised), or their depth into it (flush)
raise_t = 1.0;
// Border ring: distance from plate edge to ring
ring_inset = 4.2;
// Border ring width
ring_w = 3.7;
// Clearance between the ring and the content
inner_pad = 4.0;
// Move the content (and its holes) up (+) or down (-) by this much, for optical centring. The web UI balances a round sign on its visual weight automatically and adds this on top.
content_shift = 0.0;

/* [Mounting holes] */
// Hole X positions (list) for the top and bottom rows. [] also gives no holes.
hole_x = [24, 156];
// Where the holes go. "none" gives a plain plate. "top" sits beside the icon (or in a band above the text if there is no icon); "bottom" adds a band below the text; use both for tall signs. "middle" puts one hole in a rounded tab on each side of the sign at mid-height, clear of the content (hole_x is then ignored).
hole_rows = "top"; // [none, top, bottom, top+bottom, middle]
// Material around the hole (or countersink) in a middle tab
tab_pad = 3.0;
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
is_round = shape == "round";
pw = is_round ? diameter : width;                    // plate width
has_holes = is_round || len(hole_x) > 0;
want_top = has_holes && (hole_rows == "top" || hole_rows == "top+bottom");
want_bot = has_holes && (hole_rows == "bottom" || hole_rows == "top+bottom");
want_tabs = hole_rows == "middle";
tab_r = max(hole_d, csk_d) / 2 + tab_pad;         // radius of a side tab; its hole sits at its centre
tab_w = want_tabs ? 2 * tab_r : 0;                // how far each tab sticks out
overall_w = pw + 2 * tab_w;
band_h = max(hole_d, csk_d) + 2;                  // height reserved for a hole row
// Top holes sit beside the icon when there is one, so they only need a band of their own without it.
// On a round sign the hole is a single one at the centre, so it always has its own band.
top_band = (want_top && (is_round || !show_icon)) ? band_h + gap_icon_text : 0;
bot_band = want_bot ? band_h + line_gap : 0;
content_h = top_band + (show_icon ? icon_h + gap_icon_text : 0) + text_block_h + bot_band;
margin = ring_inset + ring_w + inner_pad;      // plate edge -> content
height = is_round ? diameter : content_h + 2 * margin;
inner_w = pw - 2 * margin;                        // inner width, or inner diameter when round
content_top = (is_round ? height / 2 + content_h / 2 : height - margin) + content_shift;
icon_y = content_top - top_band - icon_h;         // bottom of the icon
text_top = show_icon ? icon_y - gap_icon_text : content_top - top_band;   // top of the first text line
hole_ys = concat(
    want_top ? [(show_icon && !is_round) ? content_top - icon_h / 2 : content_top - band_h / 2] : [],
    want_bot ? [content_top - content_h + band_h / 2] : []);
hole_xs = is_round ? [pw / 2] : hole_x;
// Every hole as [x, y]
hole_pts = want_tabs ? [[-tab_r, height / 2], [pw + tab_r, height / 2]]
                     : [for (x = hole_xs, y = hole_ys) [x, y]];
eps = 0.01;

echo(str("plate ", overall_w, " x ", height, " mm, inner ", is_round ? "diameter " : "width ", inner_w, " mm"));

// ------------------------------------------------------------------
// Icon from the SVG, scaled to h tall and centred on the origin.
module icon_2d(h = icon_h) {
    resize([0, h], auto = true) import(icon_file, center = true);
}

// ------------------------------------------------------------------
module rrect(w, h, r) {
    offset(r = r) offset(delta = -r) square([w, h]);
}

// Plate outline inset by d from the edge: rounded rectangle or disc.
module outline_2d(d = 0) {
    if (is_round)
        translate([pw / 2, height / 2]) circle(d = diameter - 2 * d);
    else
        translate([d, d]) rrect(pw - 2 * d, height - 2 * d, corner_r - d);
}

// Rounded mounting tab on one side, blended into the plate edge. side = -1 left, +1 right.
module tab_2d(side) {
    cx = side < 0 ? -tab_r : pw + tab_r;
    hull() {
        translate([cx, height / 2]) circle(r = tab_r);
        translate([side < 0 ? 0 : pw - 1, height / 2 - tab_r]) square([1, 2 * tab_r]);
    }
}

module plate_2d() {
    difference() {
        union() {
            outline_2d();
            if (want_tabs) { tab_2d(-1); tab_2d(1); }
        }
        for (p = hole_pts) translate(p) circle(d = hole_d);
    }
}

module ring_2d() {
    difference() {
        outline_2d(ring_inset);
        outline_2d(ring_inset + ring_w);
    }
}

module text_2d() {
    for (i = [0 : n_lines - 1]) {
        baseline = text_top - cap_h - i * (cap_h + line_gap);
        translate([pw / 2, baseline])
            text(lines[i], size = text_size, font = font, halign = "center", valign = "baseline");
    }
}

module raised_2d() {
    ring_2d();
    if (show_icon)
        translate([pw / 2, icon_y + icon_h / 2])
            mirror([icon_flip ? 1 : 0, 0]) icon_2d();
    text_2d();
}

flush = style == "flush";
assert(!flush || raise_t < base_t, "flush style needs raise_t (inlay depth) smaller than base_t");
if (flush && part == "both")
    echo("flush style: export part=\"base\" and part=\"raised\" separately; a union of both is a plain slab");

module base() {
    color([1, 0.82, 0.01]) difference() {
        linear_extrude(base_t) plate_2d();
        if (csk_d > hole_d)
            for (p = hole_pts)
                translate([p[0], p[1], base_t - (csk_d - hole_d) / 2])
                    cylinder(d1 = hole_d, d2 = csk_d + 2 * eps, h = (csk_d - hole_d) / 2 + eps);
        // flush: pocket for the inlay, open at the top face
        if (flush)
            translate([0, 0, base_t - raise_t]) linear_extrude(raise_t + eps) raised_2d();
    }
}

module raised() {
    color([0.08, 0.08, 0.08])
        if (flush)
            translate([0, 0, base_t - raise_t]) linear_extrude(raise_t) raised_2d();
        else
            translate([0, 0, base_t - eps]) linear_extrude(raise_t + eps) raised_2d();
}

if (part == "base" || part == "both") base();
if (part == "raised" || part == "both") raised();
