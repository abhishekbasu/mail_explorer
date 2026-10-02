"""Composite native-resolution demo screenshots into the approved artwork.

Run with: uv run --with pillow python scripts/build_readme_images.py
Capture fresh synthetic screenshots first using capture_readme_screenshots.py.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

IMAGES = Path(__file__).resolve().parents[1] / "docs" / "images"
# Insets are measured against the approved, front-facing presentation artwork.
# Frames contain no message text; every UI pixel comes from a fresh screenshot.
PRESENTATIONS = {
    "mail-explorer-light.png": [
        ("desktop-light.png", (228, 214, 2970, 2241), 20),
    ],
    "mail-explorer-dark.png": [
        ("desktop-dark.png", (248, 188, 2966, 2221), 20),
    ],
    "mail-explorer-mobile.png": [
        ("mobile-list.png", (603, 106, 1647, 2196), 39),
        ("mobile-reader.png", (1809, 106, 2853, 2196), 39),
    ],
}


def rounded_screen(source: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    """Preserve the full screenshot, fitted to the artwork's existing screen."""
    screen = source.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    # Drawing the mask at higher density keeps the rounded glass edges smooth.
    mask = Image.new("L", (size[0] * 2, size[1] * 2), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, mask.width - 1, mask.height - 1), radius=radius * 2, fill=255
    )
    screen.putalpha(mask.resize(size, Image.Resampling.LANCZOS))
    return screen


def build() -> None:
    for filename, panels in PRESENTATIONS.items():
        with Image.open(IMAGES / "frames" / filename) as frame:
            canvas = frame.convert("RGBA")
        for screenshot, (left, top, right, bottom), radius in panels:
            size = (right - left, bottom - top)
            with Image.open(IMAGES / "screenshots" / screenshot) as source:
                # UI is rendered at 3x density, then downsampled, never enlarged.
                assert source.width >= size[0] and source.height >= size[1], screenshot
                panel = rounded_screen(source, size, radius)
            canvas.alpha_composite(panel, (left, top))
        output = IMAGES / filename
        canvas.convert("RGB").save(output, optimize=True)
        print(f"{output.relative_to(IMAGES)}: {canvas.width} x {canvas.height}")

    # Preserve the supplied diagrams' text and geometry, using their originals
    # so repeated builds never accumulate resampling or sharpening artifacts.
    for source_path in sorted((IMAGES / "mbox" / "sources").glob("*.png")):
        with Image.open(source_path) as source:
            diagram = ImageOps.exif_transpose(source).convert("RGB")
            diagram = diagram.resize((3200, 2400), Image.Resampling.LANCZOS)
            diagram = diagram.filter(ImageFilter.UnsharpMask(radius=1.2, percent=110, threshold=3))
        output = IMAGES / "mbox" / source_path.name
        diagram.save(output, optimize=True)
        print(f"mbox/{source_path.name}: {diagram.width} x {diagram.height}")


if __name__ == "__main__":
    build()
