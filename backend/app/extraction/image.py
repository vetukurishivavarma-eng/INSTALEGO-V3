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
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from app.extraction.base import ParsedDocument, ParsedPage, ParsingError

# Sharpness measured as the variance of the Laplacian. The threshold is
# deliberately low: it is meant to catch clearly unusable images, not to
# second-guess a merely mediocre scan.
BLUR_VARIANCE_THRESHOLD = 60.0
# Exposure is measured on the paper, not on the page average. A document is
# mostly blank sheet, so the mean brightness reports the sheet: a page
# photographed at a third of the exposure it needed still averages about 86,
# nowhere near any threshold a fully black image would require, and the check
# that used the mean never fired on a real underexposure. The 95th percentile
# *is* the paper, and if the paper has gone dark the photograph is
# underexposed. Across the degradation fixtures an underexposed page reads
# about 88 and the darkest still-legible one — a page with a shadow thrown
# across it — about 224, so this sits clear of both.
PAPER_LEVEL_THRESHOLD = 150
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

    paper_level = float(np.percentile(grayscale, 95))
    if paper_level < PAPER_LEVEL_THRESHOLD:
        flags.append("UNCLEAR_IMAGE")
        notes.append(f"the page is underexposed (the paper itself reads {paper_level:.0f}/255)")

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
    """Focus measure, taken after a median pass. OpenCV when present, NumPy otherwise.

    Laplacian variance counts any high-frequency energy as detail and cannot
    tell a sharp stroke from a speck of photocopier dust. Measured raw, speckle
    raised a page's focus score sevenfold and grain fivefold — so a noisy
    out-of-focus scan, which is the common case, scored as sharper than a clean
    one and was never flagged.

    A 3x3 median removes isolated outliers and leaves real strokes intact.
    After it, the same speckled page scores within a few per cent of the clean
    original, and the genuinely soft pages still fall well under the threshold.
    """
    if grayscale.shape[0] < 3 or grayscale.shape[1] < 3:
        return None

    denoised = np.asarray(
        Image.fromarray(grayscale.astype("uint8")).filter(ImageFilter.MedianFilter(3)),
        dtype=np.float64,
    )

    try:
        import cv2

        return float(cv2.Laplacian(denoised.astype("uint8"), cv2.CV_64F).var())
    except Exception:  # noqa: BLE001 - OpenCV is optional at runtime
        laplacian = (
            -4 * denoised[1:-1, 1:-1]
            + denoised[:-2, 1:-1]
            + denoised[2:, 1:-1]
            + denoised[1:-1, :-2]
            + denoised[1:-1, 2:]
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
