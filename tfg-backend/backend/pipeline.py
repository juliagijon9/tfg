"""Orquestador del pipeline de triaje de tickets: encadena LLM 1 y LLM 2."""

import logging

from backend.config import Settings, get_settings
from backend.intent_extractor import extract_intention
from backend.llm_classifier import classify_ticket
from backend.models import TriageResult, WorkItem

logger = logging.getLogger(__name__)


def triage_ticket(work_item: WorkItem, settings: Settings | None = None) -> TriageResult:
    """Ejecuta el pipeline completo de triaje sobre un ticket.

    Encadena dos LLMs secuencialmente:
      1. extract_intention → clarifica la intención real del ticket.
      2. classify_ticket → asigna departamento usando ticket original + intención.
    """
    if settings is None:
        settings = get_settings()

    intention = extract_intention(work_item, settings)
    logger.info("Intención extraída para ticket %d", work_item.id)

    classification = classify_ticket(work_item, intention, settings)
    logger.info("Ticket %d clasificado como: %s", work_item.id, classification.area)

    return TriageResult(
        work_item=work_item,
        intention=intention,
        classification=classification,
    )
