import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

# ---------------------------
# PostgreSQL configuration
# ---------------------------
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB")
PG_USER = os.getenv("POSTGRES_USER")
PG_PASS = os.getenv("POSTGRES_PASSWORD")

# ---------------------------
# Textos de los prompts (leídos desde ficheros .txt)
# ---------------------------
_PROMPTS_DIR = Path(__file__).parent / "prompts"

PROMPT_INTENTION = (_PROMPTS_DIR / "intention_prompt.txt").read_text(encoding="utf-8")
PROMPT_CLASSIFICATION = (_PROMPTS_DIR / "classification_prompt.txt").read_text(encoding="utf-8")
PROMPT_TAG = (_PROMPTS_DIR / "tag_prompt.txt").read_text(encoding="utf-8")



# ---------------------------
# Validación de entorno
# ---------------------------
def validate_env() -> None:
    required = ["POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
    for var in required:
        if not os.getenv(var):
            print(f"❌ Falta la variable de entorno: {var}")
            sys.exit(1)


# ---------------------------
# Tabla ado_config_prompt
# ---------------------------
def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_config_prompt (
                prompt_name  VARCHAR(50)  NOT NULL,
                version      SERIAL,
                created_at   TIMESTAMP    NOT NULL DEFAULT NOW(),
                prompt_text  TEXT         NOT NULL,
                PRIMARY KEY (prompt_name, version)
            );
        """)
    conn.commit()


def insert_prompt(conn, prompt_name: str, prompt_text: str) -> None:
    print(f"  ⚙️  Insertando {prompt_name}...")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.ado_config_prompt (prompt_name, prompt_text) VALUES (%s, %s);",
            (prompt_name, prompt_text)
        )
    conn.commit()


# ---------------------------
# Main
# ---------------------------
def main() -> None:
    validate_env()

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASS
    )

    ensure_table(conn)
    insert_prompt(conn, "prompt_intention", PROMPT_INTENTION)
    insert_prompt(conn, "prompt_classification", PROMPT_CLASSIFICATION)
    insert_prompt(conn, "prompt_tag", PROMPT_TAG)

    conn.close()
    print("✅ Prompts cargados correctamente.")


if __name__ == "__main__":
    main()
