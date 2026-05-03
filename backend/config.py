"""Carga y validación de variables de entorno para el módulo backend."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

# Busca el .env en la raíz del repositorio (dos niveles arriba de este fichero)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

_REQUIRED_VARS = [
    "AZURE_DEVOPS_ORG_URL",
    "AZURE_DEVOPS_PROJECT",
    "AZURE_DEVOPS_PAT",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_DEPLOYMENT",
    "POSTGRES_HOST",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
]


class Settings(BaseModel):
    """Variables de entorno necesarias para el backend."""

    AZURE_DEVOPS_ORG_URL: str
    AZURE_DEVOPS_PROJECT: str
    AZURE_DEVOPS_PAT: str
    AZURE_OPENAI_ENDPOINT: str
    AZURE_OPENAI_API_KEY: str
    AZURE_OPENAI_API_VERSION: str
    AZURE_OPENAI_DEPLOYMENT: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str


def get_settings() -> Settings:
    """Carga el .env y devuelve un objeto Settings validado.

    Lanza ValueError indicando qué variable falta si alguna no está definida.
    """
    load_dotenv(dotenv_path=_ENV_PATH)

    missing = [var for var in _REQUIRED_VARS if not os.environ.get(var)]
    if missing:
        raise ValueError(
            f"Faltan las siguientes variables de entorno: {', '.join(missing)}"
        )

    try:
        return Settings(
            AZURE_DEVOPS_ORG_URL=os.environ["AZURE_DEVOPS_ORG_URL"],
            AZURE_DEVOPS_PROJECT=os.environ["AZURE_DEVOPS_PROJECT"],
            AZURE_DEVOPS_PAT=os.environ["AZURE_DEVOPS_PAT"],
            AZURE_OPENAI_ENDPOINT=os.environ["AZURE_OPENAI_ENDPOINT"],
            AZURE_OPENAI_API_KEY=os.environ["AZURE_OPENAI_API_KEY"],
            AZURE_OPENAI_API_VERSION=os.environ["AZURE_OPENAI_API_VERSION"],
            AZURE_OPENAI_DEPLOYMENT=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            POSTGRES_HOST=os.environ["POSTGRES_HOST"],
            POSTGRES_PORT=int(os.environ.get("POSTGRES_PORT", "5432")),
            POSTGRES_DB=os.environ["POSTGRES_DB"],
            POSTGRES_USER=os.environ["POSTGRES_USER"],
            POSTGRES_PASSWORD=os.environ["POSTGRES_PASSWORD"],
        )
    except ValidationError as exc:
        bad_fields = ", ".join(e["loc"][0] for e in exc.errors())
        raise ValueError(f"Variable de entorno inválida: {bad_fields}") from exc
