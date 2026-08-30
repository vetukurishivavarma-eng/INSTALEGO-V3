"""What the extractor is asked for, and how clearly it is asked.

The field list is the extraction spec. A bare field name is a question with an
implied boundary, and where the boundary is not obvious the model answers a
different question than the one intended: asking a sanction letter for
``party_two`` returned the addressee's name *and* the address printed under it,
which is a fair reading of what was asked.

These tests cover the description mechanism that fixes that, and the two ways
it can rot: a description attached to a field nothing requests, and a
description that never reaches the prompt.
"""

from __future__ import annotations

from app.agents.document_extractor import (
    CONFUSABLE_TYPES,
    FIELD_DESCRIPTIONS,
    GENERIC_FIELDS,
    REQUIRED_FIELDS,
    DocumentExtractorAgent,
    canonical_field_for,
    describe_fields,
    required_fields_for,
)
from app.agents.base_agent import render


class TestFieldDescriptions:
    def test_a_described_field_carries_its_definition(self):
        rendered = describe_fields(["party_two"])
        assert rendered.startswith("- party_two: ")
        assert "not the address" in rendered

    def test_an_undescribed_field_is_still_listed(self):
        """Most fields are unambiguous and are left alone, so the list must
        keep working for a name with no description attached."""
        assert describe_fields(["agreement_date"]) == "- agreement_date"

    def test_the_order_of_the_requested_fields_is_preserved(self):
        names = ["party_one", "agreement_date", "party_two"]
        assert [line.split(":")[0] for line in describe_fields(names).splitlines()] == [
            "- party_one",
            "- agreement_date",
            "- party_two",
        ]

    def test_every_description_belongs_to_a_field_that_is_requested(self):
        """A description on a field nothing asks for is dead text in nobody's
        prompt, and a sign the field list moved underneath it."""
        requested = {name for names in REQUIRED_FIELDS.values() for name in names}
        requested.update(required_fields_for("UNKNOWN"))
        orphans = set(FIELD_DESCRIPTIONS) - requested
        assert not orphans, f"described but never requested: {sorted(orphans)}"

    def test_a_description_does_not_change_what_the_field_maps_to(self):
        """Descriptions clarify the question; they must not quietly redefine
        which canonical field an answer lands on."""
        assert canonical_field_for("account_holder_name") == "name"
        assert canonical_field_for("party_two") is None


class TestConfusableTypes:
    """Extraction must not depend on which of two plausible labels won.

    The sanction letter came back as AGREEMENT on one run and LOAN_APPLICATION
    on the next from identical pixels and an unchanged classifier prompt. The
    type selects the field list, so two of its fields were requested on one run
    and not the other, and the document's score moved without anything having
    changed. A sharper classifier prompt addresses the label; this addresses
    the consequence.
    """

    def test_every_member_of_a_group_asks_for_the_same_fields(self):
        for group in CONFUSABLE_TYPES:
            lists = [required_fields_for(member) for member in group]
            assert all(names == lists[0] for names in lists), group

    def test_the_widened_list_contains_what_each_member_needs(self):
        for group in CONFUSABLE_TYPES:
            widened = set(required_fields_for(group[0]))
            for member in group:
                assert set(REQUIRED_FIELDS.get(member, ())) <= widened, member

    def test_widening_does_not_duplicate_a_shared_field(self):
        """loan_amount is on both lists; asking for it twice would put two
        entries for one value into the audit chain."""
        for group in CONFUSABLE_TYPES:
            names = required_fields_for(group[0])
            assert len(names) == len(set(names)), group

    def test_a_type_outside_any_group_is_untouched(self):
        assert required_fields_for("AADHAAR") == REQUIRED_FIELDS["AADHAAR"]
        assert required_fields_for("SALARY_SLIP") == REQUIRED_FIELDS["SALARY_SLIP"]

    def test_an_unrecognised_type_still_falls_back_to_the_identity_core(self):
        """An unclassified document must not be mined speculatively for
        financial values."""
        assert required_fields_for("UNKNOWN") == GENERIC_FIELDS


class TestPromptWiring:
    def _system_prompt_for(self, document_type: str) -> str:
        agent = DocumentExtractorAgent()
        return render(
            agent.system_prompt,
            {
                "document_type": document_type,
                "required_fields": describe_fields(required_fields_for(document_type)),
            },
        )

    def test_the_description_reaches_the_prompt(self):
        prompt = self._system_prompt_for("AGREEMENT")
        assert "party_two: " in prompt
        assert "not the address block printed beneath it" in prompt

    def test_the_classifier_is_told_how_to_tell_the_confusable_types_apart(self):
        """The classifier had the same problem the extractor did: a list of
        bare type names, and no statement of what separates two of them."""
        from app.agents.base_agent import load_prompt

        # The prompt is wrapped prose, so a phrase can straddle a line break.
        prompt = " ".join(load_prompt("classifier").split())
        assert "decide by WHO ISSUED the document and WHAT IT DOES" in prompt
        assert "A letter that refers to an application is not itself an application" in prompt
        for group in CONFUSABLE_TYPES:
            for member in group:
                assert f"{member} -" in prompt, member

    def test_the_prompt_states_the_boundary_rule_in_general(self):
        """Descriptions cover the fields we know about; the rule covers the
        ones we have not measured yet."""
        prompt = self._system_prompt_for("AADHAAR")
        assert "A field holds one value" in prompt

    def test_an_undescribed_document_type_still_renders_its_fields(self):
        prompt = self._system_prompt_for("PAN")
        for name in required_fields_for("PAN"):
            assert f"- {name}" in prompt

    def test_the_stub_reads_the_same_field_list_the_model_is_given(self):
        """The mock client parses the field list back out of the prompt, so it
        is coupled to how that list is rendered. When descriptions were added
        it stopped recognising the described fields and quietly extracted less
        than it was asked for — which surfaced as unrelated failures three
        suites away, not as a parsing error."""
        from app.llm.mock import REQUESTED_FIELD

        for document_type in ("AGREEMENT", "AADHAAR", "SALARY_SLIP"):
            expected = required_fields_for(document_type)
            assert REQUESTED_FIELD.findall(describe_fields(expected)) == expected, document_type
