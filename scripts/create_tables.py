"""
Script de inicialización de base de datos.
Crea todas las tablas del pipeline desde cero (vacías).
Usar IF NOT EXISTS para no romper nada si ya existen.

Ejecución manual:
    .venv/bin/python scripts/create_tables.py

NO se ejecuta desde el API ni desde el frontend.
"""

import os
import sys

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
# Validación de entorno
# ---------------------------
def validate_env() -> None:
    required = ["POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
    for var in required:
        if not os.getenv(var):
            print(f"❌ Falta la variable de entorno: {var}")
            sys.exit(1)


# ---------------------------
# Creación de tablas
# ---------------------------
def create_tables(conn) -> None:

    with conn.cursor() as cur:

        # ---------------------------
        # Tickets de Azure DevOps
        # ---------------------------
        print("  ⚙️  ado_work_items...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_items (
                id                  BIGINT PRIMARY KEY,
                work_item_type      TEXT,
                title               TEXT,
                state               TEXT,
                created_date        TIMESTAMP,
                changed_date        TIMESTAMP,
                area_path           TEXT,
                iteration_path      TEXT,
                assigned_to         TEXT,
                tags                TEXT,
                description         TEXT,
                repro_steps         TEXT,
                acceptance_criteria TEXT
            );
        """)

        # ---------------------------
        # Embeddings
        # ---------------------------
        print("  ⚙️  ado_work_item_embeddings...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_embeddings (
                work_item_id  BIGINT PRIMARY KEY REFERENCES public.ado_work_items(id),
                embedding     DOUBLE PRECISION[],
                model         TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # ---------------------------
        # Relaciones (duplicados / relacionados)
        # ---------------------------
        print("  ⚙️  ado_work_item_relations...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_relations (
                source_id     BIGINT REFERENCES public.ado_work_items(id),
                target_id     BIGINT,
                relation_type TEXT,
                similarity    DOUBLE PRECISION,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_id, target_id)
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_source
            ON public.ado_work_item_relations(source_id);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_type
            ON public.ado_work_item_relations(relation_type);
        """)

        # ---------------------------
        # Intenciones extraídas por LLM
        # ---------------------------
        print("  ⚙️  ado_work_item_intentions...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_intentions (
                work_item_id                  BIGINT PRIMARY KEY REFERENCES public.ado_work_items(id),
                intention                     TEXT,
                model                         TEXT,
                extracted_at                  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                nivel_confianza               INTEGER CHECK (nivel_confianza BETWEEN 1 AND 4),
                nivel_confianza_justificacion TEXT
            );
        """)

        # ---------------------------
        # Clasificaciones por área
        # ---------------------------
        print("  ⚙️  ado_work_item_classifications...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_classifications (
                work_item_id   BIGINT PRIMARY KEY REFERENCES public.ado_work_items(id),
                area           TEXT      NOT NULL,
                justification  TEXT      NOT NULL,
                model          TEXT      NOT NULL,
                classified_at  TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)

        # ---------------------------
        # Tags funcionales
        # ---------------------------
        print("  ⚙️  ado_work_item_tag...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_tag (
                work_item_id     BIGINT NOT NULL REFERENCES public.ado_work_items(id),
                tag              TEXT   NOT NULL,
                model            TEXT   NOT NULL,
                extracted_tag_at TIMESTAMP NOT NULL DEFAULT NOW(),
                justificacion    TEXT,
                PRIMARY KEY (work_item_id, tag)
            );
        """)

        # ---------------------------
        # Prompts con versionado
        # ---------------------------
        print("  ⚙️  ado_config_prompt...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_config_prompt (
                prompt_name  VARCHAR(50) NOT NULL,
                version      SERIAL,
                created_at   TIMESTAMP   NOT NULL DEFAULT NOW(),
                prompt_text  TEXT        NOT NULL,
                PRIMARY KEY (prompt_name, version)
            );
        """)

        # ---------------------------
        # Historial de jobs del pipeline
        # ---------------------------
        print("  ⚙️  pipeline_jobs...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.pipeline_jobs (
                job_id      UUID PRIMARY KEY,
                type        TEXT,
                status      TEXT,
                result      JSONB,
                error       TEXT,
                started_at  TIMESTAMP,
                finished_at TIMESTAMP
            );
        """)

    conn.commit()


# ---------------------------
# Main
# ---------------------------
def main() -> None:
    validate_env()

    print(f"🔧 Conectando a {PG_HOST}:{PG_PORT}/{PG_DB}...")
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASS
    )

    print("📋 Creando tablas...")
    create_tables(conn)

    insert_initial_prompts(conn)

    conn.close()
    print("✅ Todas las tablas creadas correctamente.")


# ---------------------------
# Insertar prompts iniciales (solo si no existen)
# ---------------------------
def insert_initial_prompts(conn) -> None:
    prompts = [
        ("prompt_intention",      "prompt_intention"),
        ("prompt_classification", "prompt_classification"),
        ("prompt_tag",            "prompt_tag"),
    ]
    with conn.cursor() as cur:
        for name, text in prompts:
            cur.execute("""
                INSERT INTO public.ado_config_prompt (prompt_name, prompt_text)
                SELECT %s, %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM public.ado_config_prompt WHERE prompt_name = %s
                );
            """, (name, text, name))
            if cur.rowcount > 0:
                print(f"  ⚙️  Prompt '{name}' insertado.")
            else:
                print(f"  ⏭️  Prompt '{name}' ya existe, omitido.")
    conn.commit()


if __name__ == "__main__":
    main()
