"""The hard fixtures must themselves be trustworthy.

A degradation suite is a measuring instrument, and an instrument that drifts
tells you nothing. These tests run entirely offline and check the three
properties the measurement depends on:

- every condition produces a valid image, so a sweep cannot fail for a reason
  that has nothing to do with the model;
- the same fixture is byte-identical between runs, so a change in the numbers
  is a change in the system rather than a different random sample;
- each condition actually degrades along the axis it claims, so "blur_severe"
  is genuinely blurrier than "blur_mild" and the ordering in a report means
  something.

The last one also guards the ground truth: a truth table can only be scored
against fields the extractor is asked for, and those lists live in a different
module that will drift.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from tests.evaluation.extraction_cases import SCORING_KINDS, core_matrix, matrix
from tests.evaluation.extraction_runner import CORRECT, MISSING, WRONG, compare
from tests.fixtures.degrade import CORE_DEGRADATIONS, DEGRADATIONS
from tests.fixtures.hard_documents import CORE_DOCUMENTS, DOCUMENTS, base_image, build_variant


def _sharpness(image: Image.Image) -> float:
    grayscale = np.asarray(image.convert("L"), dtype=np.float64)
    laplacian = (
        -4 * grayscale[1:-1, 1:-1]
        + grayscale[:-2, 1:-1]
        + grayscale[2:, 1:-1]
        + grayscale[1:-1, :-2]
        + grayscale[1:-1, 2:]
    )
    return float(laplacian.var())


def _contrast(image: Image.Image) -> float:
    return float(np.asarray(image.convert("L"), dtype=np.float64).std())


# --------------------------------------------------------------------------
# The instrument works
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(DEGRADATIONS))
def test_every_degradation_produces_a_usable_image(name):
    result = DEGRADATIONS[name].apply(base_image("pan"))
    assert result.mode in {"RGB", "RGBA"}
    assert min(result.size) > 100
    # An all-white page means the transformation destroyed the document rather
    # than degrading it, which would score as a model failure.
    assert _contrast(result) > 1.0


@pytest.mark.parametrize("name", sorted(DEGRADATIONS))
def test_degradations_are_reproducible(name):
    first = DEGRADATIONS[name].apply(base_image("pan"))
    second = DEGRADATIONS[name].apply(base_image("pan"))
    assert np.array_equal(np.asarray(first), np.asarray(second))


def test_fixture_filenames_are_stable(tmp_path):
    first = build_variant("aadhaar", "phone_photo", tmp_path / "a")
    second = build_variant("aadhaar", "phone_photo", tmp_path / "b")
    assert first.name == second.name
    assert first.read_bytes() == second.read_bytes()


def test_fixture_filenames_do_not_name_the_document_type(tmp_path):
    """A filename that says "Aadhaar" lets the classifier be right for the
    wrong reason, which would flatter the number this suite measures."""
    for doc_id in DOCUMENTS:
        written = build_variant(doc_id, "clean", tmp_path)
        assert doc_id not in written.name.lower()


@pytest.mark.parametrize("fmt,suffix", [("jpg", ".jpg"), ("png", ".png"), ("pdf", ".pdf")])
def test_every_delivery_format_is_written(tmp_path, fmt, suffix):
    written = build_variant("pan", "clean", tmp_path, fmt=fmt)
    assert written.suffix == suffix
    assert written.stat().st_size > 1000


# --------------------------------------------------------------------------
# The conditions degrade along the axis they claim
# --------------------------------------------------------------------------
def test_blur_severity_is_ordered():
    clean = _sharpness(base_image("pan"))
    mild = _sharpness(DEGRADATIONS["blur_mild"].apply(base_image("pan")))
    severe = _sharpness(DEGRADATIONS["blur_severe"].apply(base_image("pan")))
    assert clean > mild > severe


def test_fade_severity_is_ordered():
    clean = _contrast(base_image("pan"))
    faded = _contrast(DEGRADATIONS["faded"].apply(base_image("pan")))
    very = _contrast(DEGRADATIONS["very_faded"].apply(base_image("pan")))
    assert clean > faded > very


def test_rotation_changes_the_page_shape():
    upright = base_image("pan")
    sideways = DEGRADATIONS["rotated_90"].apply(upright)
    assert sideways.size == (upright.size[1], upright.size[0])


def test_the_worst_case_is_the_worst_case():
    """A composite must be harder than the axes it is built from.

    Compared on contrast and against the clean page, not against blur_mild:
    the Laplacian focus measure counts jpeg and sensor noise as detail, so a
    noisy blurred page can score sharper than a clean blurred one. That is a
    property of the measure rather than of the fixture -- and a real weakness
    in the parser, recorded in test_noise_defeats_the_blur_detector below.
    """
    worst = DEGRADATIONS["worst_case"].apply(base_image("pan"))
    assert _sharpness(worst) < _sharpness(base_image("pan"))
    assert _contrast(worst) < _contrast(DEGRADATIONS["faded"].apply(base_image("pan")))


# --------------------------------------------------------------------------
# What the sweep found out about the quality check itself
# --------------------------------------------------------------------------
# Both of these were real gaps in app/extraction/image.py, found by running the
# catalogue against it and since fixed. They stay as tests because both fixes
# are threshold judgements that a future change could quietly undo.
def _flags_for(name: str) -> list[str]:
    import io

    from app.extraction.image import parse_image

    buffer = io.BytesIO()
    DEGRADATIONS[name].apply(base_image("pan")).save(buffer, format="PNG")
    return list(parse_image(buffer.getvalue()).quality_flags)


def test_an_underexposed_page_is_flagged():
    """The check used to average the whole page. A document is mostly white
    paper, so the mean measured the sheet: a page at a third of its exposure
    still averaged about 86 and never tripped a threshold meant for a black
    image. Exposure is now read off the paper itself."""
    assert "UNCLEAR_IMAGE" in _flags_for("dark")


def test_a_legible_page_with_a_shadow_across_it_is_not_flagged_as_underexposed():
    """The other side of that threshold. Uneven lighting is normal in a phone
    photograph and must not be called an exposure failure."""
    from app.extraction.image import PAPER_LEVEL_THRESHOLD

    shadowed = np.asarray(DEGRADATIONS["shadowed"].apply(base_image("pan")).convert("L"))
    assert float(np.percentile(shadowed, 95)) > PAPER_LEVEL_THRESHOLD


def test_noise_does_not_defeat_the_blur_detector():
    """Laplacian variance counts any high-frequency energy as detail, so raw
    speckle raised a page's focus score sevenfold and a noisy blurred scan --
    the common case -- scored sharper than a clean one. A median pass restores
    the ordering."""
    from app.extraction.image import _laplacian_variance

    clean = _laplacian_variance(np.asarray(base_image("pan").convert("L"), dtype=np.float64))
    for name in ("speckled", "noisy"):
        measured = _laplacian_variance(
            np.asarray(DEGRADATIONS[name].apply(base_image("pan")).convert("L"), dtype=np.float64)
        )
        # Within a few per cent of the clean page, rather than multiples of it.
        assert measured < clean * 1.1, name
        assert "UNCLEAR_IMAGE" not in _flags_for(name), name


def test_the_median_pass_does_not_blunt_a_genuinely_sharp_page():
    """Denoising costs a little real detail. The margin over the threshold has
    to survive it, or the fix for noise becomes a false-positive machine."""
    from app.extraction.image import BLUR_VARIANCE_THRESHOLD, _laplacian_variance

    clean = _laplacian_variance(np.asarray(base_image("pan").convert("L"), dtype=np.float64))
    assert clean > BLUR_VARIANCE_THRESHOLD * 5


def test_every_condition_marked_flag_is_caught_by_the_parser():
    """The catalogue's own expectations, checked against the parser rather than
    against a live model: a page marked "flag" must be one the pipeline refuses
    to read confidently."""
    for name, condition in DEGRADATIONS.items():
        if condition.expectation != "flag":
            continue
        assert "UNCLEAR_IMAGE" in _flags_for(name), name


# --------------------------------------------------------------------------
# The ground truth is scoreable
# --------------------------------------------------------------------------
def test_truth_fields_are_fields_the_extractor_actually_asks_for():
    """Truth that the extractor is never asked for would score as a permanent
    miss and measure a specification gap, not degradation."""
    from app.agents.document_extractor import canonical_field_for, required_fields_for

    for doc_id, document in DOCUMENTS.items():
        requested = {
            canonical_field_for(name) or name
            for expected_type in document.expected_type
            for name in required_fields_for(expected_type)
        }
        missing = set(document.truth) - requested
        assert not missing, f"{doc_id} expects {missing}, which is never requested"


def test_decoys_name_a_field_that_has_a_truth():
    for doc_id, document in DOCUMENTS.items():
        assert set(document.decoys) <= set(document.truth), doc_id


def test_amount_fields_are_scored_as_amounts():
    """Off-profile fields normalise as free text, where "42,000.00" and "42000"
    differ. The scoring kinds exist to stop that being reported as a wrong read."""
    assert compare("net_salary", "42000", "42,000.00") == CORRECT
    assert compare("closing_balance", "125000", "1,25,000.00") == CORRECT
    assert "net_salary" in SCORING_KINDS


def test_a_wrong_value_is_not_scored_as_missing():
    assert compare("date_of_birth", "12/04/1998", "12/04/1997") == WRONG
    assert compare("date_of_birth", "12/04/1998", None) == MISSING
    assert compare("date_of_birth", "12/04/1998", "1998-04-12") == CORRECT


def test_the_scorer_normalises_rather_than_trusting_a_stored_value():
    """So that fixing a normaliser can be re-scored against a recorded run
    (``--rescore``) instead of costing another sweep. The sanction letter's own
    phrasing is the case that made this matter."""
    assert compare("loan_amount", "500000", "Rs. 5,00,000/- (Rupees Five Lakh only)") == CORRECT


def test_a_declared_illegible_value_counts_as_missing():
    """The transcription prompt asks the model to mark what it cannot read.
    Scoring that as a wrong read would penalise it for being honest."""
    assert compare("name", "Ravi Kumar", "R. [ILLEGIBLE]") == MISSING


def test_address_is_scored_on_similarity_not_equality():
    """An address read as "12 MG Rd" is a correct read. Scoring it as a failure
    would bury the real ones."""
    assert compare(
        "current_address",
        "12, M.G. Road, Bengaluru - 560001",
        "12 MG Rd, Bengaluru 560001",
    ) == CORRECT
    assert compare(
        "current_address",
        "12, M.G. Road, Bengaluru - 560001",
        "88 Park Street, Kolkata 700016",
    ) == WRONG


# --------------------------------------------------------------------------
# The matrix
# --------------------------------------------------------------------------
def test_the_core_matrix_fits_a_free_tier_daily_allowance():
    """Roughly three model calls a variant against a 200-a-day cap: the sweep
    has to leave room to repeat a few and still run the other suite."""
    variants = core_matrix()
    assert len(variants) == len(CORE_DOCUMENTS) * len(CORE_DEGRADATIONS)
    assert len(variants) * 3 < 150


def test_the_full_matrix_covers_every_document_and_condition():
    variants = matrix()
    assert len(variants) == len(DOCUMENTS) * len(DEGRADATIONS)
    assert {variant.doc_id for variant in variants} == set(DOCUMENTS)
    assert {variant.degradation for variant in variants} == set(DEGRADATIONS)


def test_every_variant_knows_what_correct_behaviour_is():
    for variant in matrix():
        assert variant.condition.expectation in {"read", "flag"}
        assert variant.document.truth
