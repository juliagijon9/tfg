"""Punto de entrada del clasificador de tickets de Azure DevOps.

Uso:
    python -m backend.main --top 10
"""

import argparse

from rich.console import Console
from rich.progress import track
from rich.table import Table

from backend.config import get_settings
from backend.devops_client import fetch_recent_tickets
from backend.llm_classifier import classify_ticket

console = Console()


def _build_table(rows: list[tuple[str, str, str, str]]) -> Table:
    """Construye la tabla Rich con las clasificaciones."""
    table = Table(show_header=True, header_style="bold cyan", show_lines=True)
    table.add_column("ID", style="dim", width=8, justify="right")
    table.add_column("Título", width=62)
    table.add_column("Área", width=30)
    table.add_column("Justificación", width=50)

    for ticket_id, title, area, justification in rows:
        table.add_row(ticket_id, title, area, justification)

    return table


def main() -> None:
    """Recupera tickets de Azure DevOps, los clasifica con OpenAI e imprime una tabla."""
    parser = argparse.ArgumentParser(
        description="Clasificador de tickets de Azure DevOps con Azure OpenAI."
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        metavar="N",
        help="Número de tickets más recientes a clasificar (por defecto: 10).",
    )
    args = parser.parse_args()

    settings = get_settings()

    console.print(f"\n[bold]Recuperando {args.top} tickets de Azure DevOps…[/bold]")
    tickets = fetch_recent_tickets(top=args.top, settings=settings)

    if not tickets:
        console.print("[yellow]No se encontraron tickets con los filtros actuales.[/yellow]")
        return

    console.print(f"[green]{len(tickets)} ticket(s) recuperados.[/green] Clasificando…\n")

    rows: list[tuple[str, str, str, str]] = []
    for ticket in track(tickets, description="Clasificando tickets"):
        classification = classify_ticket(ticket, settings)
        title_truncated = ticket.title[:60] + "…" if len(ticket.title) > 60 else ticket.title
        rows.append((
            str(ticket.id),
            title_truncated,
            classification.area,
            classification.justification,
        ))

    console.print()
    console.print(_build_table(rows))


if __name__ == "__main__":
    main()
