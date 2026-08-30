"""Agents: narrow, orchestrated components with defined inputs and outputs.

None of these call each other. The workflow decides what runs and in what
order, which is what makes the pipeline reproducible and auditable rather than
emergent.
"""

from app.agents.base_agent import AgentRun, BaseAgent, load_prompt, prompt_version
from app.agents.discrepancy_reasoner import DiscrepancyReasonerAgent
from app.agents.document_classifier import DocumentClassifierAgent
from app.agents.document_extractor import DocumentExtractorAgent
from app.agents.evidence_verifier import EvidenceVerifierAgent
from app.agents.profile_builder import ProfileBuilderAgent
from app.agents.qa_agent import QAAgent
from app.agents.report_mapper import ReportMapperAgent

__all__ = [
    "AgentRun",
    "BaseAgent",
    "DiscrepancyReasonerAgent",
    "DocumentClassifierAgent",
    "DocumentExtractorAgent",
    "EvidenceVerifierAgent",
    "ProfileBuilderAgent",
    "QAAgent",
    "ReportMapperAgent",
    "load_prompt",
    "prompt_version",
]
