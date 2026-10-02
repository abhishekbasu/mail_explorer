# README images

The app screenshots contain only fictional people, subjects, and messages, with
addresses at `example.com`. The featured sender is Avery Morgan, with the address
`avery@example.com`; the name, avatar, signature, and table entries all use Avery.
The capture script creates a temporary demo archive
and cache and starts an isolated server. It never loads messages from the
project's `mailbox/` directory or its existing index. The temporary archive is
deleted after capture.

The four source screenshots show the running app in Chromium at **3× pixel
density**: 4,320 × 3,240 pixels for desktop and 1,290 × 2,796 for mobile.
The approved blue backgrounds and glass framing originated with the built-in
image generation tool. The final HD presentations use **Python/Pillow
compositing**, as requested, placing fresh screenshots into reusable, text-free
frames. UI pixels are downsampled from the native captures, rather than enlarged
or generated. PNG output is lossless. The decorative backdrops are enlarged from
the approved artwork.

| Presentation | Resolution | Source screenshots |
| --- | --- | --- |
| [Light desktop](mail-explorer-light.png) | 3,200 × 2,400 | [Desktop light](screenshots/desktop-light.png) |
| [Dark desktop](mail-explorer-dark.png) | 3,200 × 2,400 | [Desktop dark](screenshots/desktop-dark.png) |
| [Compact screens](mail-explorer-mobile.png) | 3,456 × 2,304 | [Thread list](screenshots/mobile-list.png), [Markdown reader](screenshots/mobile-reader.png) |

The [light](frames/mail-explorer-light.png), [dark](frames/mail-explorer-dark.png),
and [mobile](frames/mail-explorer-mobile.png) frames contain no mail or names.

## Mbox illustrations

The three supplied diagrams were moved from `mbox_explainer/` into the
documentation assets. The README versions are **3,200 × 2,400 pixels**, upscaled
with Lanczos resampling and lightly sharpened. They preserve the original
content, layout, filenames, and order:

1. [Overall organization](mbox/1_overall_organization.png)
2. [Inside one message](mbox/2_one_mbox_file.png)
3. [Parsing details](mbox/3_parsing_mbox.png)

The README pairs them with explanations of message separators, headers, MIME
parts, escaped body lines, and indexing. Unmodified originals are retained in
[`mbox/sources/`](mbox/sources/) so rebuilding never accumulates resampling
artifacts.

## Rebuild the HD images

From the repository root:

```sh
uv run --with playwright playwright install chromium
uv run --with playwright python scripts/capture_readme_screenshots.py
uv run --with pillow python scripts/build_readme_images.py
```

Playwright and Pillow are temporary command dependencies; the app's runtime
dependencies are unchanged. The capture script saves only synthetic screenshots
in `docs/images/screenshots/`. The compositor uses those screenshots, the
text-free frames, and the supplied diagram originals to rebuild all six README
images at their existing paths.

Verification includes the exported PNG dimensions, README links, visual
inspection, and OCR checks for the replacement name and diagram content.

## Original backdrop generation prompts

Mode: built-in image generation for the original approved presentation artwork.
The prompts below produced the framing and backgrounds reused by the HD
compositor. Final HD exports use the Python/Pillow workflow above; no API/CLI
image generation is required to rebuild them.

### mail-explorer-light.png

```text
Use case: compositing
Asset type: polished README screenshot presentation for Mail Explorer.
Input image 1: the exact real application screenshot to insert, not a loose visual reference.
Primary request: create a beautiful, calm, futuristic product presentation around this screenshot.
Composition/framing: landscape 4:3 canvas. One large, perfectly front-facing app screenshot, with all four edges visible, occupying about 88% of the canvas width. Preserve the screenshot's original 4:3 aspect ratio. Place it inside a restrained, rounded, thin frosted-glass window frame with a soft shadow. Keep the entire UI legible and sharp. No perspective tilt.
Scene/backdrop: luminous ice-blue to pale aqua gradient with a subtle, softly flowing blue light behind the window; generous but modest margins, refined studio lighting.
Color palette: #03045E, #023E8A, #0077B6, #0096C7, #00B4D8, #48CAE4, #90E0EF, #ADE8F4, #CAF0F8. Emphasize pale ice and cyan with a restrained navy shadow.
Constraints: composite the supplied screenshot intact. Do not redraw, restyle, simplify, replace, translate, or invent any part of the screenshot. Preserve every existing label, message, name, example.com address, icon, button, table, and layout. Only add framing and background outside the screenshot. Do not crop any part of the screenshot.
Avoid: extra text, slogans, branding, browser URL bars, fake controls, hands, desks, devices, exaggerated neon, busy decoration, watermarks.
```

### mail-explorer-dark.png

```text
Use case: compositing
Asset type: polished README screenshot presentation for Mail Explorer.
Input image 1: the exact real dark-mode application screenshot to insert unchanged.
Primary request: present the app's dark appearance, frosted Archives panel and quote-copy menu in a beautiful, futuristic product image.
Composition/framing: landscape 4:3 canvas. One large, perfectly front-facing screenshot with all four edges visible, occupying about 88% of the canvas width. Preserve its original 4:3 aspect ratio. A very thin, dark translucent glass frame with rounded outer corners and a delicate cyan edge highlight. Subtle grounded shadow, no perspective tilt.
Scene/backdrop: deep midnight navy with soft, diffused cobalt and cyan light, elegantly restrained. Match the light-mode presentation's layout and level of polish.
Color palette: #03045E, #023E8A, #0077B6, #0096C7, #00B4D8, #48CAE4. Dark navy dominates; cyan is only a quiet accent.
Constraints: composite the screenshot intact; preserve every UI pixel and the screenshot's dark colors, existing text, fictional names, example.com addresses, layout, original Archives sidebar and existing Copy menu exactly. Do not redraw, restyle, simplify or invent UI. Keep both original Copy menu options fully visible. Do not crop the screenshot. Only add framing and background outside it.
Avoid: extra text, slogans, branding, fake controls, new popovers, browser URL bars, devices, dramatic neon, decorative machinery, watermarks.
```

### mail-explorer-mobile.png

```text
Use case: compositing
Asset type: polished README screenshot presentation for Mail Explorer's responsive mobile layout.
Input image 1: exact real mobile thread-list screenshot, left panel.
Input image 2: exact real mobile Markdown reading screenshot, right panel.
Primary request: create a beautiful two-panel presentation showing browsing and reading on a compact screen.
Composition/framing: landscape 3:2 canvas. Two tall, equally sized, perfectly front-facing screenshot panels placed side by side with balanced margins and a modest gap. Both original full screenshots must be entirely visible, retaining their original 430:932 aspect ratio. They should occupy most of the canvas height. Each has a very thin, rounded frosted-glass outer frame and a soft shadow. No perspective distortion and no overlap.
Scene/backdrop: a soft, luminous blue gradient flowing from pale ice to light aqua, with a quiet navy tint at one edge; refined and calm, matching the desktop presentations.
Color palette: #03045E, #0077B6, #0096C7, #00B4D8, #90E0EF, #ADE8F4, #CAF0F8.
Constraints: insert each provided screenshot intact without redrawing, restyling, simplifying, translating or inventing any UI. Preserve every label, fictional name, example.com address, table, icon and message exactly. Screenshots must remain sharp and legible. Only add framing and background outside them.
Avoid: extra text, headlines, slogans, phone hardware, notches, status bars, fake controls, clipped screenshot edges, hands, overlapping panels, excessive neon, watermarks.
```
