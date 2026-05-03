"""Punto de entrada del extractor de intención de tickets.

Uso:
    python -m backend.extract_intention_main --since 2026-04-30 --limit 10
"""

import argparse
import logging

from rich.console import Console
from rich.progress import track
from rich.table import Table

from backend.config import get_settings
from backend.db_client import fetch_tickets_after, upsert_intention
from backend.intent_extractor import extract_intention

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

console = Console()


def _build_table(rows: list[tuple[str, str, str, str]]) -> Table:
    """Construye la tabla Rich con los resultados de extracción de intención."""
    table = Table(show_header=True, header_style="bold cyan", show_lines=True)
    table.add_column("ID", style="dim", width=8, justify="right")
    table.add_column("Tipo", width=14)
    table.add_column("Título", width=52)
    table.add_column("Intención", width=70)

    for ticket_id, work_item_type, title, intention in rows:
        table.add_row(ticket_id, work_item_type, title, intention)

    return table


def main() -> None:
    """Recupera tickets de PostgreSQL, extrae su intención con OpenAI e imprime una tabla."""
    parser = argparse.ArgumentParser(
        description="Extractor de intención de tickets de Azure DevOps."
    )
    parser.add_argument(
        "--since",
        type=str,
        default="2026-04-30",
        metavar="YYYY-MM-DD",
        help="Fecha de corte ISO: recupera tickets con created_date > SINCE (por defecto: 2026-04-30).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Número máximo de tickets a procesar (por defecto: sin límite).",
    )
    args = parser.parse_args()

    settings = get_settings()

    console.print(f"\n[bold]Recuperando tickets posteriores a {args.since}…[/bold]")
    work_items = fetch_tickets_after(cutoff_date=args.since, settings=settings)

    if args.limit is not None:
        work_items = work_items[: args.limit]

    if not work_items:
        console.print("[yellow]No se encontraron tickets con los filtros actuales.[/yellow]")
        return

    console.print(f"[green]{len(work_items)} ticket(s) recuperados.[/green] Extrayendo intención…\n")

    rows: list[tuple[str, str, str, str]] = []
    for work_item in track(work_items, description="Extrayendo intención"):
        intention = extract_intention(work_item, settings)
        upsert_intention(
            work_item_id=work_item.id,
            intention=intention.intention,
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            settings=settings,
        )
        title_truncated = (
            work_item.title[:50] + "…" if len(work_item.title) > 50 else work_item.title
        )
        rows.append((
            str(work_item.id),
            work_item.work_item_type,
            title_truncated,
            intention.intention,
        ))

    console.print()
    console.print(_build_table(rows))


if __name__ == "__main__":
    main()
