"""Clasificador de tickets usando Azure OpenAI."""

import json

from openai import AzureOpenAI
from pydantic import ValidationError

from backend.config import Settings, get_settings
from backend.models import Classification, Intention, WorkItem
from backend.prompts.classification_prompt import SYSTEM_PROMPT


def classify_ticket(
    work_item: WorkItem,
    intention: Intention,
    settings: Settings | None = None,
) -> Classification:
    """Envía un WorkItem y su intención clarificada a Azure OpenAI y devuelve la clasificación.

    El clasificador recibe el ticket original y la intención depurada por el LLM previo,
    usando la intención como guía principal de la decisión.
    """
    if settings is None:
        settings = get_settings()

    client = AzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
    )

    user_payload = (
        "=== TICKET ORIGINAL ===\n"
        f"Tipo: {work_item.work_item_type}\n"
        f"Título: {work_item.title}\n"
        f"Etiquetas: {work_item.tags or '(ninguna)'}\n"
        f"Descripción: {work_item.description or '(sin descripción)'}\n"
        f"Pasos para reproducir: {work_item.repro_steps or '(no aplican)'}\n"
        f"Criterios de aceptación: {work_item.acceptance_criteria or '(no aplican)'}\n"
        "\n"
        "=== INTENCIÓN CLARIFICADA (LLM previo) ===\n"
        f"{intention.intention}\n"
    )

    response = client.chat.completions.create(
        model=settings.AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ],
        temperature=0.0,
        max_tokens=200,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or ""

    try:
        return Classification.model_validate_json(raw)
    except (ValidationError, json.JSONDecodeError) as exc:
        return Classification(
            area="Team QA",
            justification=f"ERROR DE PARSEO: {str(exc)[:150]}",
        )
