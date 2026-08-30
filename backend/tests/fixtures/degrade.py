"""Image degradations, applied to a clean page to make it hard to read.

The existing fixtures all render as crisp black text on white paper with an
unambiguous ``Label: value`` on every line. A pipeline scores perfectly on
those and still fails on the first real scan, because what a bank actually
receives is a phone photograph taken at an angle under a desk lamp, or the
third generation of a photocopy, or a page with a registrar's stamp sitting
across the date of birth.

Each degradation here is one named, deterministic transformation with the
severity baked into its name, so a run can say precisely which one broke
extraction rather than reporting an average over a bag of noise. Randomness is
seeded from the name, so the same fixture is byte-identical between runs and a
regression is a real change rather than a different sample.

Nothing here tries to be photorealistic. The point is to move one axis at a
time far enough that a weakness shows.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


def _seeded(name: str) -> np.random.Generator:
    """A generator derived from a name, not from Python's salted hash(), so a
    fixture is identical between processes as well as between runs."""
    seed = int.from_bytes(name.encode("utf-8")[:8].ljust(8, b"\0"), "little")
    return np.random.default_rng(seed % (2**32))


# --------------------------------------------------------------------------
# Single-axis transformations
# --------------------------------------------------------------------------
def rotate(image: Image.Image, degrees: float) -> Image.Image:
    """Page fed in crooked. Expands the canvas so no text is cropped away."""
    return image.rotate(
        degrees, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255)
    )


def blur(image: Image.Image, radius: float) -> Image.Image:
    """Out of focus, as a hand-held photograph of a page usually is."""
    return image.filter(ImageFilter.GaussianBlur(radius=radius))


def fade(image: Image.Image, contrast: float, brightness: float = 1.0) -> Image.Image:
    """Low contrast: a tired toner cartridge, or thermal paper that has aged.

    The two parameters compound, and past roughly (0.25, 1.25) they erase the
    page outright. A blank sheet is not a hard fixture -- it tests only whether
    a blank sheet is detected -- so the catalogue stays on the legible side of
    that edge and ``test_every_degradation_produces_a_usable_image`` holds it
    there.
    """
    faded = ImageEnhance.Contrast(image).enhance(contrast)
    if brightness != 1.0:
        faded = ImageEnhance.Brightness(faded).enhance(brightness)
    return faded


def darken(image: Image.Image, factor: float) -> Image.Image:
    """Underexposed: photographed in a badly lit branch office."""
    return ImageEnhance.Brightness(image).enhance(factor)


def jpeg(image: Image.Image, quality: int) -> Image.Image:
    """Recompression artefacts, from a document that has been mailed around."""
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def noise(image: Image.Image, sigma: float, *, name: str = "noise") -> Image.Image:
    """Sensor grain. Uniform across the page, unlike speckle."""
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    grain = _seeded(name).normal(0.0, sigma, array.shape)
    return Image.fromarray(np.clip(array + grain, 0, 255).astype("uint8"))


def speckle(image: Image.Image, density: float, *, name: str = "speckle") -> Image.Image:
    """Photocopier dust: isolated black and white pixels, not gaussian grain."""
    array = np.asarray(image.convert("RGB"), dtype="uint8").copy()
    generator = _seeded(name)
    height, width = array.shape[:2]
    count = int(height * width * density)
    rows = generator.integers(0, height, count)
    columns = generator.integers(0, width, count)
    values = generator.choice([0, 255], count)
    array[rows, columns] = values[:, None]
    return Image.fromarray(array)


def downscale(image: Image.Image, factor: float, *, restore: bool = True) -> Image.Image:
    """Resolution thrown away. With restore=False the page also trips the
    minimum-dimension check, which is a different failure from mere softness."""
    width, height = image.size
    small = image.resize(
        (max(1, int(width * factor)), max(1, int(height * factor))), Image.LANCZOS
    )
    return small.resize((width, height), Image.LANCZOS) if restore else small


def shadow(image: Image.Image, strength: float = 0.55) -> Image.Image:
    """Uneven lighting: the photographer's own shadow across one corner."""
    width, height = image.size
    y, x = np.mgrid[0:height, 0:width]
    # A diagonal ramp, darkest at the bottom-right, never fully black.
    ramp = 1.0 - strength * ((x / width) * 0.6 + (y / height) * 0.4)
    array = np.asarray(image.convert("RGB"), dtype=np.float32) * ramp[:, :, None]
    return Image.fromarray(np.clip(array, 0, 255).astype("uint8"))


def perspective(image: Image.Image, tilt: float = 0.08) -> Image.Image:
    """Keystone distortion from photographing a page that is not flat-on."""
    width, height = image.size
    source = [(0, 0), (width, 0), (width, height), (0, height)]
    target = [
        (width * tilt, height * tilt * 0.4),
        (width * (1 - tilt * 0.25), 0),
        (width, height * (1 - tilt * 0.3)),
        (width * tilt * 0.5, height),
    ]
    return image.transform(
        (width, height),
        Image.PERSPECTIVE,
        _perspective_coefficients(target, source),
        resample=Image.BICUBIC,
        fillcolor=(255, 255, 255),
    )


def _perspective_coefficients(source, target) -> tuple[float, ...]:
    """Solve for the eight coefficients PIL's PERSPECTIVE transform wants."""
    matrix = []
    for (sx, sy), (tx, ty) in zip(source, target, strict=True):
        matrix.append([sx, sy, 1, 0, 0, 0, -tx * sx, -tx * sy])
        matrix.append([0, 0, 0, sx, sy, 1, -ty * sx, -ty * sy])
    a = np.array(matrix, dtype=np.float64)
    b = np.array([coordinate for point in target for coordinate in point], dtype=np.float64)
    return tuple(np.linalg.lstsq(a, b, rcond=None)[0])


def crease(image: Image.Image, folds: int = 2) -> Image.Image:
    """Fold lines from a document that was posted in an envelope."""
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    height, width = array.shape[:2]
    for index in range(1, folds + 1):
        position = int(height * index / (folds + 1))
        band = slice(max(0, position - 6), min(height, position + 6))
        array[band] *= 0.72
        edge = slice(max(0, position - 12), max(0, position - 6))
        array[edge] = np.clip(array[edge] * 1.12, 0, 255)
    return Image.fromarray(np.clip(array, 0, 255).astype("uint8"))


def bleed_through(image: Image.Image, opacity: float = 0.16) -> Image.Image:
    """Faint mirrored text from the reverse side of thin paper."""
    reverse = image.transpose(Image.FLIP_LEFT_RIGHT)
    return Image.blend(image.convert("RGB"), reverse.convert("RGB"), opacity)


def stamp(image: Image.Image, text: str = "VERIFIED", *, angle: float = -22.0) -> Image.Image:
    """A semi-transparent office stamp sitting across the value column.

    Placed over the middle of the page rather than in a margin: the question
    worth asking is whether a value stays readable underneath ink that was
    never meant to be there.
    """
    base = image.convert("RGBA")
    width, height = base.size
    side = int(min(width, height) * 0.55)
    layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    ink = (170, 30, 40, 130)
    draw.ellipse([4, 4, side - 4, side - 4], outline=ink, width=max(3, side // 60))
    draw.ellipse(
        [side // 12, side // 12, side - side // 12, side - side // 12],
        outline=ink,
        width=max(2, side // 90),
    )
    font = font_of(int(side * 0.15))
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((side - (box[2] - box[0])) / 2, (side - (box[3] - box[1])) / 2),
        text,
        font=font,
        fill=ink,
    )
    layer = layer.rotate(angle, resample=Image.BICUBIC, expand=False)
    base.alpha_composite(layer, (int(width * 0.32), int(height * 0.28)))
    return base.convert("RGB")


def handwriting(
    image: Image.Image, notes: dict[str, tuple[int, int]] | None = None
) -> Image.Image:
    """A clerk's pen correction in the margin, which must not be mistaken for
    the printed value."""
    annotated = image.convert("RGB")
    draw = ImageDraw.Draw(annotated)
    width, height = annotated.size
    font = font_of(int(height * 0.028), italic=True)
    ink = (20, 40, 160)
    default = {"as per record": (int(width * 0.62), int(height * 0.22))}
    for text, (x, y) in (notes or default).items():
        draw.text((x, y), text, font=font, fill=ink)
        draw.line(
            [(x - 10, y + int(height * 0.03)), (x + int(width * 0.2), y + int(height * 0.03))],
            fill=ink,
            width=2,
        )
    return annotated


def binarize(image: Image.Image, threshold: int = 150) -> Image.Image:
    """Hard black-and-white, as a fax or a cheap scanner produces. Thin strokes
    disappear entirely, which is what makes it worth testing."""
    grayscale = np.asarray(image.convert("L"))
    binary = np.where(grayscale > threshold, 255, 0).astype("uint8")
    return Image.fromarray(binary).convert("RGB")


def font_of(size: int, *, bold: bool = False, italic: bool = False, serif: bool = False):
    """A real typeface where the machine has one, so glyph shapes resemble a
    printed document rather than a synthetic bitmap face."""
    candidates: list[str] = []
    if serif:
        candidates += ["timesbd.ttf" if bold else "times.ttf", "georgia.ttf"]
    if italic:
        candidates += ["ariali.ttf", "calibrii.ttf"]
    candidates += [
        "arialbd.ttf" if bold else "arial.ttf",
        "calibrib.ttf" if bold else "calibri.ttf",
        "segoeui.ttf",
        "DejaVuSans.ttf",
    ]
    for candidate in candidates:
        for prefix in ("C:/Windows/Fonts/", ""):
            try:
                return ImageFont.truetype(prefix + candidate, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Degradation:
    """One named condition, and what it is meant to find out."""

    name: str
    description: str
    apply: Callable[[Image.Image], Image.Image]
    # What a correct system should do. "read" means extraction is still
    # expected to succeed; "flag" means the page is bad enough that the honest
    # answer is a quality flag rather than a confident value.
    expectation: str = "read"


def _compose(*steps: Callable[[Image.Image], Image.Image]) -> Callable[[Image.Image], Image.Image]:
    def run(image: Image.Image) -> Image.Image:
        for step in steps:
            image = step(image)
        return image

    return run


DEGRADATIONS: dict[str, Degradation] = {
    item.name: item
    for item in [
        Degradation("clean", "no degradation; the control", lambda im: im),
        Degradation(
            "skew_mild", "rotated 3 degrees, a page fed in slightly crooked",
            lambda im: rotate(im, 3),
        ),
        Degradation("skew_severe", "rotated 12 degrees", lambda im: rotate(im, 12)),
        Degradation("rotated_90", "scanned sideways", lambda im: rotate(im, 90)),
        Degradation("upside_down", "scanned upside down", lambda im: rotate(im, 180)),
        Degradation("blur_mild", "slightly out of focus", lambda im: blur(im, 1.6)),
        Degradation("blur_severe", "badly out of focus", lambda im: blur(im, 4.0), "flag"),
        Degradation("faded", "low contrast, a worn photocopy", lambda im: fade(im, 0.40, 1.10)),
        Degradation("very_faded", "barely visible toner", lambda im: fade(im, 0.22, 1.10), "flag"),
        Degradation("dark", "underexposed photograph", lambda im: darken(im, 0.35), "flag"),
        Degradation("jpeg_low", "heavy recompression", lambda im: jpeg(im, 18)),
        Degradation("noisy", "sensor grain", lambda im: noise(im, 26.0, name="noisy")),
        Degradation(
            "speckled", "photocopier dust", lambda im: speckle(im, 0.006, name="speckled")
        ),
        Degradation(
            "low_resolution", "resolution thrown away and restored",
            lambda im: downscale(im, 0.22),
        ),
        Degradation(
            "tiny", "a genuinely small image, below the useful-size floor",
            lambda im: downscale(im, 0.35, restore=False), "flag",
        ),
        Degradation("shadowed", "uneven lighting across the page", lambda im: shadow(im)),
        Degradation("perspective", "photographed at an angle", lambda im: perspective(im, 0.09)),
        Degradation("creased", "fold lines from an envelope", lambda im: crease(im)),
        Degradation(
            "bleed_through", "text showing through thin paper", lambda im: bleed_through(im)
        ),
        Degradation("stamped", "an office stamp across the values", lambda im: stamp(im)),
        Degradation(
            "annotated", "a handwritten note beside the printed values",
            lambda im: handwriting(im),
        ),
        Degradation(
            "faxed", "binarised, as a fax or a cheap scanner produces",
            _compose(lambda im: downscale(im, 0.5), lambda im: binarize(im, 165)),
        ),
        # Composites: how documents actually arrive, several axes at once.
        Degradation(
            "phone_photo", "angled photograph: perspective, shadow, blur, jpeg",
            _compose(
                lambda im: perspective(im, 0.07),
                lambda im: shadow(im, 0.4),
                lambda im: blur(im, 1.2),
                lambda im: jpeg(im, 45),
            ),
        ),
        Degradation(
            "bad_photocopy", "third-generation copy: faded, skewed, specked, creased",
            _compose(
                lambda im: fade(im, 0.4, 1.2),
                lambda im: rotate(im, 2.5),
                lambda im: speckle(im, 0.004, name="bad_photocopy"),
                lambda im: crease(im),
            ),
        ),
        Degradation(
            "worst_case", "everything at once; expected to be refused, not guessed",
            _compose(
                lambda im: perspective(im, 0.1),
                lambda im: fade(im, 0.35, 1.10),
                lambda im: blur(im, 3.0),
                lambda im: noise(im, 18.0, name="worst_case"),
                lambda im: jpeg(im, 20),
            ),
            "flag",
        ),
    ]
}

# A smaller matrix for a run on a rate-limited free endpoint: one representative
# of each axis rather than every severity.
CORE_DEGRADATIONS = (
    "clean",
    "skew_severe",
    "blur_severe",
    "very_faded",
    "low_resolution",
    "stamped",
    "phone_photo",
    "bad_photocopy",
    "worst_case",
)
