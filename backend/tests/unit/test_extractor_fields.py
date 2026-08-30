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
    FIELD_DESCRIPTIONS,
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
