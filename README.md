# Custom sign

A two-colour 3D-printable sign: yellow base plate with a raised black border,
icon and text. Everything is parametric in OpenSCAD.

## Files

| File | Purpose |
|------|---------|
| `custom_sign.scad` | The sign. All settings are parameters at the top. |
| `icon.svg` | The icon the sign uses. Swap or regenerate to change the picture. |
| `trace_silhouette.py` | Turns any black-on-white image into an SVG the sign can use (Pillow + potrace). |
| `requirements.txt` | Python packages for the trace script. |
| `docker/patches/` | Alpine's patches for building OpenSCAD 2021.01 against current libraries. |
| `.venv/` | Local Python environment with those packages installed. |
| `web/` | Web UI (FastAPI) that wraps the above. |
| `Dockerfile`, `compose.yml` | Container build for the web UI. |

## Changing the text and layout

Open `custom_sign.scad` in OpenSCAD and edit the parameters at the top, or use
Window > Customizer. The main ones:

- `lines` is a list of strings, one per line. Add or remove entries; the plate
  height is worked out from the content.
- `text_size` is fixed, not auto-fit, because OpenSCAD 2021 cannot measure
  text width. The default 10.35 makes the longest default line just fill the
  width. If a new line runs into the border, lower it.
- `font` is a fontconfig name. `fc-list` shows what is installed.
- `show_icon`, `icon_h`, `icon_flip` control the icon.
- `width`, `corner_r`, `base_t`, `raise_t`, `ring_inset`, `ring_w`,
  `inner_pad` set the plate and border.
- `hole_x` is a list of hole X positions (`[]` for none), with `hole_d` and
  `csk_d` for the hole and countersink. `hole_rows` puts the holes at the
  top (beside the icon, or in a band above the text when there is no icon),
  at the bottom in a band below the text, or both. Use both for tall signs.

## Changing the icon

OpenSCAD cannot trace bitmaps, so a script does it, using Pillow to prepare
the image and [potrace](https://potrace.sourceforge.net/) to trace it. Start
from any image with a dark shape on a light background (PNG, JPG;
transparency counts as white):

```sh
./.venv/bin/python trace_silhouette.py image.png icon.svg
```

Then re-render in OpenSCAD. Useful options:

- `--crop L,T,R,B` picks the shape out of a larger image, in source pixels.
- `--invert` for a light shape on a dark background.
- `--threshold N` (0-255, default 128) if the edge lands in the wrong place.
- `--smooth N` (0 to 1.334, default 0) rounds corners into curves. Leave it
  at 0: OpenSCAD 2021 turns each curve into dozens of segments, which makes
  the STL several times larger for no visible gain.
- `--min-area N` drops specks below N pixels.

Run with `--help` for the full list. The SVG's size and position do not
matter: the `.scad` centres it and scales it to `icon_h`. To keep several
icons, trace to different filenames and set `icon_file` to the one you
want.

The default `icon.svg` (a dog) came from the classic "Dog on premises" sign image with
`--crop 150,135,300,245`.

The script needs the `potrace` command on the PATH (`dnf install potrace`,
`apt install potrace`, `apk add potrace`).

Any SVG from elsewhere works too, as long as the shape is a filled path rather
than strokes.

## Exporting for printing

The `part` parameter selects what is generated:

```sh
openscad -D 'part="base"'   -o custom_sign_base.stl   custom_sign.scad
openscad -D 'part="raised"' -o custom_sign_raised.stl custom_sign.scad
openscad -D 'part="both"'   -o custom_sign.stl        custom_sign.scad
```

For a multi-material printer, load the base and raised STLs as one object and
assign a colour to each. For a single extruder, print `both` and swap filament
at the top of the base plate (3 mm by default).

## Web UI (container)

A small web front end wraps the same `.scad` and tracer: fill in the text and
plate settings, optionally upload an image or SVG for the icon, press Render,
check the preview, and download the combined STL. The download is only
enabled for the settings currently shown; changing anything marks the preview
stale until you render again. A build plate size (default 256 x 256 mm)
is checked against the rendered sign in both orientations and warns if it
will not fit. It is stateless; each request renders into a temp directory
that is deleted afterwards. Nothing is stored.

Build and run with podman or docker:

```sh
podman build -t printed_sign_generator .
podman run --rm -p 8000:8000 --read-only --tmpfs /tmp printed_sign_generator
# or
podman compose up --build
```

Then open http://localhost:8000. The image is Alpine based and kept small:
OpenSCAD 2021.01 is built from source in the first stage as a headless
binary (no Qt, no OpenGL), so the runtime stage holds only that binary, its
C libraries, the URW fonts (Nimbus Sans Narrow among them), potrace, Python
and the web app. The result is about 105 MB. See the Dockerfile for the
exact package list. The first build compiles OpenSCAD and takes a few
minutes; later builds reuse the cached stage.

The 3D preview in the page loads three.js from a CDN. Without internet
access the download still works, only the preview is skipped.

Environment variables: `RENDER_TIMEOUT` (seconds, default 180) caps a single
OpenSCAD render.

To run the web app without a container (needs `openscad` on the PATH):

```sh
./.venv/bin/pip install -r web/requirements.txt
./.venv/bin/uvicorn web.app:app --reload
```

## Setting up the Python environment again

If `.venv` is missing or broken:

```sh
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```
