"""Photographs and scans supplied directly as JPG or PNG.

An image has no text layer by definition, so every page produced here is
marked as needing vision. What this module adds is a measured opinion on
whether the image is worth reading at all: a phone photo that is badly out of
focus should raise a quality flag before the model is asked to transcribe it,
because a confident answer from a blurred document is the worst outcome
available.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.extraction.base import ParsedDocument, ParsedPage, ParsingError

# Sharpness measured as the variance of the Laplacian. The threshold is
# deliberately low: it is meant to catch clearly unusable images, not to
# second-guess a merely mediocre scan.
BLUR_VARIANCE_THRESHOLD = 60.0
DARK_MEAN_THRESHOLD = 45
# A document is mostly white paper, so a high mean brightness is normal and
# says nothing about legibility. What does matter is whether any ink is
# present at all: a blank or completely washed-out page has almost none.
MIN_INK_FRACTION = 0.0005
INK_THRESHOLD = 160
MIN_USEFUL_DIMENSION = 600
MAX_DIMENSION = 1800


def parse_image(source: str | bytes) -> ParsedDocument:
    try:
        image = Image.open(io.BytesIO(source)) if isinstance(source, bytes) else Image.open(source)
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ParsingError(f"could not open the image: {exc}", code="CORRUPTED_FILE") from exc

    # Phone cameras record orientation in EXIF rather than in the pixels.
    image = ImageOps.exif_transpose(image)
    original_size = image.size
    quality_flags, notes, sharpness = _assess(image)

    prepared = _prepare(image)
    buffer = io.BytesIO()
    prepared.save(buffer, format="PNG")

    page = ParsedPage(
        page_number=1,
        text="",
        width=float(original_size[0]),
        height=float(original_size[1]),
        has_text_layer=False,
        needs_ocr=True,
        image_bytes=buffer.getvalue(),
        label="Image",
        notes=notes,
    )
    return ParsedDocument(
        pages=[page],
        page_count=1,
        source_format="image",
        is_readable="UNCLEAR_IMAGE" not in quality_flags,
        quality_flags=quality_flags,
        metadata={
            "original_width": original_size[0],
            "original_height": original_size[1],
            "sharpness": sharpness,
        },
    )


def _assess(image: Image.Image) -> tuple[list[str], list[str], float | None]:
    flags: list[str] = []
    notes: list[str] = []

    width, height = image.size
    if min(width, height) < MIN_USEFUL_DIMENSION:
        flags.append("UNCLEAR_IMAGE")
        notes.append(f"low resolution ({width}x{height}); small print may not be legible")

    grayscale = np.asarray(image.convert("L"), dtype=np.float64)
    if grayscale.size == 0:
        return flags, notes, None

    mean = float(grayscale.mean())
    if mean < DARK_MEAN_THRESHOLD:
        flags.append("UNCLEAR_IMAGE")
        notes.append("image is very dark")

    ink_fraction = float((grayscale < INK_THRESHOLD).mean())
    if ink_fraction < MIN_INK_FRACTION:
        flags.append("UNCLEAR_IMAGE")
        notes.append("almost no legible marks were found on the page")

    sharpness = _laplacian_variance(grayscale)
    if sharpness is not None and sharpness < BLUR_VARIANCE_THRESHOLD:
        flags.append("UNCLEAR_IMAGE")
        notes.append(f"image appears out of focus (sharpness {sharpness:.1f})")

    return list(dict.fromkeys(flags)), notes, sharpness


def _laplacian_variance(grayscale: np.ndarray) -> float | None:
    """Focus measure. Uses OpenCV when present, NumPy otherwise."""
    try:
        import cv2

        return float(cv2.Laplacian(grayscale.astype("uint8"), cv2.CV_64F).var())
    except Exception:  # noqa: BLE001 - OpenCV is optional at runtime
        if grayscale.shape[0] < 3 or grayscale.shape[1] < 3:
            return None
        laplacian = (
            -4 * grayscale[1:-1, 1:-1]
            + grayscale[:-2, 1:-1]
            + grayscale[2:, 1:-1]
            + grayscale[1:-1, :-2]
            + grayscale[1:-1, 2:]
        )
        return float(laplacian.var())


def _prepare(image: Image.Image) -> Image.Image:
    """Downscale oversized images and drop alpha, which some models reject."""
    prepared = image.convert("RGB")
    width, height = prepared.size
    longest = max(width, height)
    if longest > MAX_DIMENSION:
        scale = MAX_DIMENSION / longest
        prepared = prepared.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
    return prepared
