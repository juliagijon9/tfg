"""Extractor de intención de tickets usando Azure OpenAI."""

import json
import logging

from openai import AzureOpenAI
from pydantic import ValidationError

from backend.config import Settings
from backend.models import Intention, WorkItem
from backend.prompts.intent_extraction_prompt import INTENT_EXTRACTION_PROMPT

logger = logging.getLogger(__name__)


def _build_user_payload(work_item: WorkItem) -> str:
    """Construye el texto de entrada al LLM a partir de los campos del WorkItem."""
    return (
        f"Tipo: {work_item.work_item_type}\n"
        f"Título: {work_item.title}\n"
        f"Etiquetas: {work_item.tags or '(ninguna)'}\n"
        f"Descripción: {work_item.description or '(sin descripción)'}\n"
        f"Pasos para reproducir: {work_item.repro_steps or '(no aplican)'}\n"
        f"Criterios de aceptación: {work_item.acceptance_criteria or '(no aplican)'}"
    )


def extract_intention(work_item: WorkItem, settings: Settings) -> Intention:
    """Envía un WorkItem a Azure OpenAI y devuelve su intención clarificada.

    Si la respuesta no puede parsearse como Intention válida, devuelve un
    fallback con el prefijo [ERROR DE PARSEO] y el título original truncado.
    """
    client = AzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
    )

    user_payload = _build_user_payload(work_item)

    response = client.chat.completions.create(
        model=settings.AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": INTENT_EXTRACTION_PROMPT},
            {"role": "user", "content": user_payload},
        ],
        temperature=0.1,
        max_tokens=300,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or ""

    try:
        return Intention.model_validate_json(raw)
    except (ValidationError, json.JSONDecodeError) as exc:
        logger.warning("Error parseando intención para ticket %d: %s", work_item.id, exc)
        return Intention(
            intention=f"[ERROR DE PARSEO] {work_item.title[:200]}"
        )
