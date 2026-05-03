"""Punto de entrada del pipeline completo de triaje de tickets.

Uso:
    python -m backend.triage_main --since 2026-04-30 --limit 10
"""

import argparse
import logging
from collections import Counter

from rich.console import Console
from rich.progress import track
from rich.table import Table

from backend.config import get_settings
from backend.db_client import fetch_tickets_after
from backend.models import TriageResult
from backend.pipeline import triage_ticket

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

console = Console()


def _build_table(results: list[TriageResult]) -> Table:
    """Construye la tabla Rich con los resultados del pipeline de triaje."""
    table = Table(show_header=True, header_style="bold cyan", show_lines=True)
    table.add_column("ID", style="dim", width=8, justify="right")
    table.add_column("Tipo", width=12)
    table.add_column("Título", width=42)
    table.add_column("Intención", width=55)
    table.add_column("Área", width=28)
    table.add_column("Justificación", width=40)

    for r in results:
        title = r.work_item.title
        title_truncated = title[:40] + "…" if len(title) > 40 else title
        table.add_row(
            str(r.work_item.id),
            r.work_item.work_item_type,
            title_truncated,
            r.intention.intention,
            r.classification.area,
            r.classification.justification,
        )

    return table


def _print_summary(results: list[TriageResult]) -> None:
    """Imprime un resumen con distribución por área y conteo de fallbacks."""
    console.print("\n[bold]── Resumen ──[/bold]")
    console.print(f"Total procesados: [green]{len(results)}[/green]")

    area_counts: Counter[str] = Counter(r.classification.area for r in results)
    for area, count in area_counts.most_common():
        console.print(f"  {area}: {count}")

    fallbacks = sum(
        1 for r in results if r.classification.justification.startswith("ERROR DE PARSEO")
    )
    if fallbacks:
        console.print(f"\n[yellow]⚠ Fallbacks por error de parseo: {fallbacks}[/yellow]")


def main() -> None:
    """Recupera tickets de PostgreSQL, ejecuta el pipeline de triaje e imprime resultados."""
    parser = argparse.ArgumentParser(
        description="Pipeline de triaje de tickets: extracción de intención + clasificación."
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
        default=10,
        metavar="N",
        help="Número máximo de tickets a procesar (por defecto: 10).",
    )
    args = parser.parse_args()

    settings = get_settings()

    console.print(f"\n[bold]Recuperando tickets posteriores a {args.since}…[/bold]")
    work_items = fetch_tickets_after(cutoff_date=args.since, settings=settings)
    work_items = work_items[: args.limit]

    if not work_items:
        console.print("[yellow]No se encontraron tickets con los filtros actuales.[/yellow]")
        return

    console.print(
        f"[green]{len(work_items)} ticket(s) recuperados.[/green] "
        "Ejecutando pipeline (LLM 1 → LLM 2)…\n"
    )

    results: list[TriageResult] = []
    for work_item in track(work_items, description="Triaje en progreso"):
        result = triage_ticket(work_item, settings)
        results.append(result)

    console.print()
    console.print(_build_table(results))
    _print_summary(results)


if __name__ == "__main__":
    main()
