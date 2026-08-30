"""Mapping the canonical analysis onto a bank's report schema.

Mapping is done in Python by default, because it is a rename-and-copy problem
with a correct answer and no need for language. The agent exists for the case
Python cannot cover: a bank template that asks for a section this system has no
field for, declared in the template as ``"mapping": "agent"``.

Even then the agent's output is filtered — only keys the schema declares
survive, and identifiers, amounts and severities are overwritten from the
analysis afterwards, so a mapping pass can never quietly alter a number.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.base_agent import TEMPERATURE_MAPPING, AgentRun, BaseAgent, clip, render

logger = logging.getLogger(__name__)

MAX_PAYLOAD_CHARS = 9000
NOT_AVAILABLE = "NOT_AVAILABLE"


class MappedSection(BaseModel):
    """A single mapped section. Free-form because the schema decides its shape."""

    model_config = ConfigDict(extra="allow")

    content: dict[str, Any] = Field(default_factory=dict)


class ReportMapperAgent(BaseAgent):
    prompt_name = "report_mapper"
    temperature = TEMPERATURE_MAPPING

    def map_section(
        self,
        section_name: str,
        section_schema: dict[str, Any],
        analysis_payload: dict[str, Any],
    ) -> AgentRun[MappedSection]:
        system = render(
            self.system_prompt,
            {
                "analysis": clip(
                    json.dumps(analysis_payload, separators=(",", ":"), default=str),
                    MAX_PAYLOAD_CHARS,
                ),
                "schema": json.dumps(section_schema, separators=(",", ":")),
            },
        )
        prompt = (
            f"Map the analysis into the section named {section_name}. Populate only the keys "
            f"the schema declares. Use {NOT_AVAILABLE} where the analysis has no value. "
            "Return the section object under the key 'content'."
        )
        run = self._run(MappedSection, prompt=prompt, system=system)
        run.data = MappedSection(
            content=_keep_declared(run.data.content, section_schema)
        )
        return run


def _keep_declared(content: dict[str, Any], section_schema: dict[str, Any]) -> dict[str, Any]:
    """Drop anything the schema did not ask for.

    A mapping agent that adds a key is a mapping agent writing report content,
    which is exactly what this design forbids.
    """
    declared = section_schema.get("fields")
    if not isinstance(declared, dict):
        return content if isinstance(content, dict) else {}

    kept = {key: content.get(key, NOT_AVAILABLE) for key in declared}
    dropped = set(content) - set(declared)
    if dropped:
        logger.warning("report mapper returned undeclared keys, dropped: %s", sorted(dropped))
    return kept
