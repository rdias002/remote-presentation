"""Thumbnail cache + placeholder renderer.

Controllers write PNGs into a cache directory that FastAPI serves at /thumbs.
The PowerPoint backend exports real slide images here; the mock backend draws
labelled placeholders so the PWA can be developed without PowerPoint.
"""

from __future__ import annotations

from pathlib import Path

# 16:9 (width/height ≈ 1.777) is the common deck aspect ratio and the default
# used by the mock/placeholder path. The real PowerPoint backend passes the
# deck's actual aspect so previews are never distorted.
DEFAULT_ASPECT = 16 / 9


def thumb_height(width: int, aspect: float = DEFAULT_ASPECT) -> int:
    """Pixel height for a given width and aspect (aspect = width / height)."""
    if aspect <= 0:
        aspect = DEFAULT_ASPECT
    return max(1, round(width / aspect))


def thumb_filename(deck_id: object, index: int) -> str:
    """Per-deck namespaced filename so two decks never share a cache file."""
    return f"d{deck_id}-slide-{index}.png"


def thumb_url(deck_id: object, index: int, version: object = 0) -> str:
    """Served path for a slide thumbnail.

    The filename is namespaced by ``deck_id`` so distinct decks (e.g. two
    simultaneous slideshows) can never collide on the same cache file. The
    ``version`` is a cache-busting token bumped when a deck's own thumbnails are
    re-exported (slide count / size change), forcing the browser to refetch.
    """
    return f"/thumbs/{thumb_filename(deck_id, index)}?v={version}"


def make_placeholder_png(path: Path, index: int, total: int, title: str, width: int) -> None:
    """Draw a simple labelled placeholder slide (mock backend only)."""
    from PIL import Image, ImageDraw

    height = thumb_height(width)
    # Deterministic, pleasant-ish background per slide.
    hue = (index * 47) % 360
    bg = _hsl_to_rgb(hue, 0.35, 0.22)
    accent = _hsl_to_rgb(hue, 0.55, 0.65)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline=accent, width=max(2, width // 120))

    big = f"{index}"
    draw.text((width * 0.07, height * 0.12), big, fill=accent)
    draw.text((width * 0.07, height * 0.62), title[:40], fill=(235, 235, 235))
    draw.text((width * 0.07, height * 0.80), f"slide {index} of {total}", fill=(180, 180, 180))

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")


def _hsl_to_rgb(h: float, s: float, light: float) -> tuple[int, int, int]:
    import colorsys

    r, g, b = colorsys.hls_to_rgb(h / 360.0, light, s)
    return int(r * 255), int(g * 255), int(b * 255)
