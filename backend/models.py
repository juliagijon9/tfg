"""Modelos Pydantic para tickets de Azure DevOps y clasificaciones del LLM."""

import html
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class _HTMLStripper(HTMLParser):
    """Parser interno que acumula el texto plano de un fragmento HTML."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def _strip_html(raw: str) -> str:
    """Elimina etiquetas HTML y devuelve texto plano normalizado."""
    stripper = _HTMLStripper()
    stripper.feed(raw)
    text = stripper.get_text()
    # Colapsa espacios/saltos de línea múltiples
    return re.sub(r"\s+", " ", text).strip()


AreaLiteral = Literal[
    "I2 Airplane Team",
    "I2 Ecommerce Team",
    "I2 MAD Team BI",
    "I2 VISEO App / I2 VISEO Team",
    "Team MKT I2",
    "Team QA",
    "Teams BFM",
]


class Ticket(BaseModel):
    """Representa un work item de Azure DevOps."""

    id: int
    title: str
    description: str

    @field_validator("description", mode="before")
    @classmethod
    def clean_description(cls, v: object) -> str:
        """Limpia etiquetas HTML y normaliza None a cadena vacía."""
        if v is None:
            return ""
        return _strip_html(str(v))


class Classification(BaseModel):
    """Resultado de clasificación devuelto por el LLM."""

    area: AreaLiteral
    justification: str

    @field_validator("justification")
    @classmethod
    def truncate_justification(cls, v: str) -> str:
        """Garantiza que la justificación no supere 200 caracteres."""
        return v[:200]


def _clean_html_field(v: object) -> Optional[str]:
    """Limpia etiquetas HTML, decodifica entidades y colapsa espacios. None → None."""
    if v is None:
        return None
    text = re.sub(r"<[^>]+>", " ", str(v))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip() or None


class WorkItem(BaseModel):
    """Refleja una fila de public.ado_work_items tal como la devuelve PostgreSQL."""

    id: int
    work_item_type: str
    title: str
    state: Optional[str] = None
    created_date: datetime
    changed_date: Optional[datetime] = None
    area_path: Optional[str] = None
    iteration_path: Optional[str] = None
    assigned_to: Optional[str] = None
    tags: Optional[str] = None
    description: Optional[str] = None
    repro_steps: Optional[str] = None
    acceptance_criteria: Optional[str] = None

    @field_validator("description", "repro_steps", "acceptance_criteria", mode="before")
    @classmethod
    def clean_html_fields(cls, v: object) -> Optional[str]:
        """Limpia HTML de los campos de texto enriquecido de Azure DevOps."""
        return _clean_html_field(v)


class Intention(BaseModel):
    """Intención clarificada extraída por el LLM a partir de un WorkItem."""

    intention: str

    @field_validator("intention")
    @classmethod
    def normalize_intention(cls, v: str) -> str:
        """Recorta a 600 caracteres y colapsa saltos de línea sobrantes."""
        v = re.sub(r"\n+", " ", v).strip()
        return v[:600]


class TriageResult(BaseModel):
    """Resultado completo del pipeline de triaje: ticket + intención + clasificación."""

    work_item: WorkItem
    intention: Intention
    classification: Classification
