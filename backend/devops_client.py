"""Cliente Azure DevOps para recuperar los work items más recientes."""

from azure.devops.connection import Connection
from azure.devops.v7_1.work_item_tracking.models import Wiql
from msrest.authentication import BasicAuthentication

from backend.config import Settings, get_settings
from backend.models import Ticket

# Estados excluidos de la consulta WIQL (modificar aquí para cambiar el filtro)
EXCLUDED_STATES = ("Removed", "Closed")


def fetch_recent_tickets(top: int = 10, settings: Settings | None = None) -> list[Ticket]:
    """Recupera los `top` work items más recientes del proyecto Azure DevOps.

    Ordena por fecha de modificación descendente y excluye los estados
    definidos en EXCLUDED_STATES.
    """
    if settings is None:
        settings = get_settings()

    credentials = BasicAuthentication("", settings.AZURE_DEVOPS_PAT)
    connection = Connection(base_url=settings.AZURE_DEVOPS_ORG_URL, creds=credentials)
    wit_client = connection.clients.get_work_item_tracking_client()

    excluded = ", ".join(f"'{s}'" for s in EXCLUDED_STATES)
    wiql_query = Wiql(
        query=(
            f"SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = '{settings.AZURE_DEVOPS_PROJECT}' "
            f"AND [System.State] NOT IN ({excluded}) "
            f"ORDER BY [System.ChangedDate] DESC"
        )
    )

    result = wit_client.query_by_wiql(wiql_query, top=top)
    ids = [ref.id for ref in (result.work_items or [])]

    if not ids:
        return []

    items = wit_client.get_work_items(
        ids=ids,
        fields=["System.Id", "System.Title", "System.Description"],
    )

    tickets: list[Ticket] = []
    for item in items:
        fields = item.fields
        tickets.append(
            Ticket(
                id=fields["System.Id"],
                title=fields.get("System.Title") or "",
                description=fields.get("System.Description") or "",
            )
        )

    return tickets
