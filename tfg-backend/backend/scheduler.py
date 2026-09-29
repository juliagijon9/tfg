"""Planificador diario del pipeline completo."""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000").rstrip("/")
SCHEDULE_TIMES = os.getenv("PIPELINE_SCHEDULE_TIMES", "08:00,14:00").split(",")
TIMEZONE_NAME = os.getenv("TZ", "Europe/Madrid")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def next_run(now: datetime) -> datetime:
    """Devuelve la siguiente ocurrencia de cualquiera de las horas configuradas."""
    schedules = []
    for schedule_time in SCHEDULE_TIMES:
        try:
            hour, minute = map(int, schedule_time.strip().split(":"))
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError
        except ValueError as exc:
            raise ValueError(
                "PIPELINE_SCHEDULE_TIMES debe ser una lista HH:MM separada por comas"
            ) from exc

        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        schedules.append(scheduled if scheduled > now else scheduled + timedelta(days=1))
    return min(schedules)


def run_pipeline() -> None:
    request = Request(
        f"{BACKEND_URL}/pipeline/run-all", data=b"", method="POST"
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
            logger.info("Pipeline diario iniciado (job %s).", payload["job_id"])
    except HTTPError as exc:
        if exc.code == 409:
            logger.warning("Pipeline diario omitido: ya hay otro job en ejecución.")
            return
        logger.error("No se pudo iniciar el pipeline: HTTP %s.", exc.code)
    except URLError as exc:
        logger.error("Backend no disponible: %s", exc.reason)


def main() -> None:
    timezone = ZoneInfo(TIMEZONE_NAME)
    logger.info(
        "Planificador activo: pipeline completo diario a las %s (%s).",
        ", ".join(SCHEDULE_TIMES),
        TIMEZONE_NAME,
    )
    while True:
        now = datetime.now(timezone)
        scheduled = next_run(now)
        logger.info("Próxima ejecución: %s.", scheduled.isoformat())
        time.sleep((scheduled - now).total_seconds())
        run_pipeline()


if __name__ == "__main__":
    main()
