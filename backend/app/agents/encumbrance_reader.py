"""Reading an encumbrance certificate as the ledger it is.

Every other extraction in this system pulls named scalar fields off a page,
which is the right shape for a card or a payslip and the wrong shape for a
certificate whose entire content is a table. An EC lists every registered
transaction affecting a property over a stated period; read as a list, it can
establish a chain of title from a single document, where the deeds otherwise
have to be gathered one by one and may simply not all exist.

So this is a second, narrow pass that runs only on encumbrance certificates.
It costs one model call and it is the only place in the pipeline that asks for
structure rather than fields.

The rows are read, not interpreted. Whether a mortgage in 2021 matters is a
question for the rules; whether the table says there was one is a question for
this agent, and the two are kept apart on purpose.
"""

from __future__ import annotations

import logging

from app.agents.base_agent import TEMPERATURE_EXTRACTION, AgentRun, BaseAgent, clip
from app.extraction.base import ParsedDocument
from app.llm.client import ImageContent
from app.schemas.extraction import EncumbranceLedger

logger = logging.getLogger(__name__)

# An EC covering thirty years runs to several pages of table, and every one of
# them carries transactions, so this reads further into the document than the
# classifier does.
MAX_PAGES = 12
MAX_CHARS = 16000
MAX_IMAGES = 4


class EncumbranceReaderAgent(BaseAgent):
    prompt_name = "encumbrance_reader"
    temperature = TEMPERATURE_EXTRACTION

    def read(self, parsed: ParsedDocument) -> AgentRun[EncumbranceLedger]:
        pages = [p for p in parsed.pages if p.text or p.image_bytes][:MAX_PAGES]
        text = clip(
            "\n\n".join(f"[page {p.page_number}]\n{p.text}" for p in pages if p.text),
            MAX_CHARS,
        )
        images = [
            ImageContent(data=p.image_bytes, media_type=p.image_media_type)
            for p in pages
            if p.image_bytes
        ][:MAX_IMAGES]

        if not text and not images:
            return AgentRun(
                data=EncumbranceLedger(
                    notes="no readable content was produced by parsing"
                ),
                model="none",
                prompt_version=self.version,
                attempts=0,
            )

        prompt = (
            "Read this encumbrance certificate. Return every transaction row in "
            "the table, in the order printed, and the period the certificate "
            "covers. A certificate that records no transactions is not an error: "
            "return an empty list and say so in summary."
        )
        return self._run(
            EncumbranceLedger, prompt=prompt, text=text or None, images=images or None
        )
