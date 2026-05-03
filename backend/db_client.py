"""Cliente PostgreSQL síncrono para recuperar work items de la base de datos."""

import logging
from typing import Any

import psycopg2

from backend.config import Settings, get_settings
from backend.models import WorkItem

logger = logging.getLogger(__name__)

RECENT_TICKETS_QUERY = """
    SELECT id, work_item_type, title, state, created_date,
           changed_date, area_path, iteration_path, assigned_to,
           tags, description, repro_steps, acceptance_criteria
    FROM public.ado_work_items
    WHERE created_date > %s
    ORDER BY created_date DESC;
"""

_COLUMNS = [
    "id", "work_item_type", "title", "state", "created_date",
    "changed_date", "area_path", "iteration_path", "assigned_to",
    "tags", "description", "repro_steps", "acceptance_criteria",
]


def fetch_tickets_after(cutoff_date: str, settings: Settings | None = None) -> list[WorkItem]:
    """Recupera los work items creados después de `cutoff_date` (formato 'YYYY-MM-DD').

    Usa psycopg2 con parámetro posicional (%s) para evitar inyección SQL.
    """
    if settings is None:
        settings = get_settings()

    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(RECENT_TICKETS_QUERY, (cutoff_date,))
            rows = cur.fetchall()
    finally:
        conn.close()

    logger.info("Recuperados %d tickets posteriores a %s", len(rows), cutoff_date)

    work_items: list[WorkItem] = []
    for row in rows:
        data: dict[str, Any] = dict(zip(_COLUMNS, row))
        work_items.append(WorkItem(**data))

    return work_items


def upsert_intention(
    work_item_id: int,
    intention: str,
    model: str,
    settings: Settings | None = None,
) -> None:
    """Guarda o actualiza la intención de un work item en la base de datos."""
    if settings is None:
        settings = get_settings()

    conn = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        dbname=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ado_work_item_intentions (work_item_id, intention, model)
                VALUES (%s, %s, %s)
                ON CONFLICT (work_item_id) DO UPDATE SET
                    intention    = EXCLUDED.intention,
                    model        = EXCLUDED.model,
                    extracted_at = CURRENT_TIMESTAMP;
                """,
                (work_item_id, intention, model),
            )
        conn.commit()
    finally:
        conn.close()
