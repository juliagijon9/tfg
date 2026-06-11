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

load_dotenv()  # Carga las variables del fichero .env

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
                id                  BIGINT PRIMARY KEY,   -- ID del ticket en Azure DevOps
                work_item_type      TEXT,                 -- Tipo: Bug, Feature, Task, etc.
                title               TEXT,
                state               TEXT,                 -- Estado: Active, Closed, Resolved, etc.
                created_date        TIMESTAMP,
                changed_date        TIMESTAMP,
                area_path           TEXT,                 -- Ruta jerárquica del área en ADO (ej. "Proyecto\\Equipo")
                iteration_path      TEXT,                 -- Sprint o iteración asignada
                assigned_to         TEXT,                 -- Nombre del responsable
                tags                TEXT,                 -- Tags originales de ADO (texto libre separado por ;)
                description         TEXT,                 -- Descripción en HTML (se limpia antes de mandar al LLM)
                repro_steps         TEXT,                 -- Pasos para reproducir en HTML (solo relevante en Bugs)
                acceptance_criteria TEXT                  -- Criterios de aceptación en HTML
            );
        """)

        # ---------------------------
        # Embeddings
        # ---------------------------
        print("  ⚙️  ado_work_item_embeddings...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_embeddings (
                work_item_id  BIGINT PRIMARY KEY REFERENCES public.ado_work_items(id),  -- FK al ticket
                embedding     DOUBLE PRECISION[],   -- Vector numérico de alta dimensión (text-embedding-3-large = 3072 dims)
                model         TEXT,                 -- Nombre del modelo que generó el embedding
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # ---------------------------
        # Relaciones (duplicados / relacionados)
        # ---------------------------
        print("  ⚙️  ado_work_item_relations...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_relations (
                source_id     BIGINT REFERENCES public.ado_work_items(id),  -- Ticket origen de la comparación
                target_id     BIGINT,               -- Ticket destino (0 = marcador de "procesado sin relación")
                relation_type TEXT,                 -- "duplicate" (>=0.90) o "related" (>=0.80) o NULL si target_id=0
                similarity    DOUBLE PRECISION,     -- Puntuación de similitud coseno entre los dos embeddings
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_id, target_id)  -- Un par (origen, destino) es único
            );
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_source
            ON public.ado_work_item_relations(source_id);   -- Índice para acelerar búsquedas por ticket origen
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_relations_type
            ON public.ado_work_item_relations(relation_type);  -- Índice para filtrar por tipo de relación
        """)

        # ---------------------------
        # Intenciones extraídas por LLM
        # ---------------------------
        print("  ⚙️  ado_work_item_intentions...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_intentions (
                work_item_id                  BIGINT PRIMARY KEY REFERENCES public.ado_work_items(id),
                intention                     TEXT,           -- Frase que resume qué quiere conseguir el ticket
                model                         TEXT,           -- Modelo LLM que extrajo la intención
                extracted_at                  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                nivel_confianza               INTEGER CHECK (nivel_confianza BETWEEN 1 AND 4),  -- 1=Crítico, 2=Insuficiente, 3=Suficiente, 4=Excelente
                nivel_confianza_justificacion TEXT            -- Frase explicando por qué se asignó ese nivel
            );
        """)

        # ---------------------------
        # Clasificaciones por área
        # ---------------------------
        print("  ⚙️  ado_work_item_classifications...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_classifications (
                work_item_id   BIGINT PRIMARY KEY REFERENCES public.ado_work_items(id),
                area           TEXT      NOT NULL,   -- Área del equipo asignada (ej. "I2 Ecommerce Team")
                justification  TEXT      NOT NULL,   -- Explicación del LLM de por qué ese área
                model          TEXT      NOT NULL,   -- Modelo LLM que clasificó el ticket
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
                tag              TEXT   NOT NULL,    -- Nombre del tag funcional (ej. "PAYMENT", "BACKEND")
                model            TEXT   NOT NULL,    -- Modelo LLM que asignó el tag
                extracted_tag_at TIMESTAMP NOT NULL DEFAULT NOW(),
                justificacion    TEXT,               -- Justificación individual para este tag concreto
                PRIMARY KEY (work_item_id, tag)      -- Un ticket no puede tener el mismo tag dos veces
            );
        """)

        # ---------------------------
        # Tickets modelo para clasificación (pata negra)
        # ---------------------------
        print("  ⚙️  ado_work_item_classifications_models...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_work_item_classifications_models (
                id                  BIGINT PRIMARY KEY,   -- ID del ticket en Azure DevOps (único, no se puede repetir)
                work_item_type      TEXT,                 -- Tipo: Bug, Feature, Task, etc.
                title               TEXT,
                area_path           TEXT,                 -- Área original en Azure DevOps
                iteration_path      TEXT,
                area                TEXT,                 -- Área correcta asignada por el experto (campo clave para clasificación)
                tags                TEXT,
                description         TEXT,
                repro_steps         TEXT,
                acceptance_criteria TEXT
            );
        """)

        # ---------------------------
        # Modelos de IA disponibles
        # ---------------------------
        print("  ⚙️  ado_config_models...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_config_models (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL,           -- Nombre legible: "GPT-4o Mini"
                deployment  TEXT NOT NULL UNIQUE,    -- Nombre técnico del deployment en Azure OpenAI
                description TEXT,                    -- Breve descripción y uso recomendado
                active      BOOLEAN NOT NULL DEFAULT TRUE,
                created_at  TIMESTAMP DEFAULT NOW()
            );
        """)

        # ---------------------------
        # Prompts con versionado
        # ---------------------------
        print("  ⚙️  ado_config_prompt...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.ado_config_prompt (
                prompt_name  VARCHAR(50) NOT NULL,
                version      SERIAL,                -- Se autoincrementa con cada INSERT; los scripts cargan siempre MAX(version)
                created_at   TIMESTAMP   NOT NULL DEFAULT NOW(),
                prompt_text  TEXT        NOT NULL,
                model_id     INTEGER     REFERENCES public.ado_config_models(id),  -- Modelo de IA asociado a esta versión del prompt
                PRIMARY KEY (prompt_name, version)
            );
        """)

        # Añadir model_id si la tabla ya existía sin esa columna (migración segura)
        cur.execute("""
            ALTER TABLE public.ado_config_prompt
            ADD COLUMN IF NOT EXISTS model_id INTEGER REFERENCES public.ado_config_models(id);
        """)

        # ---------------------------
        # Historial de jobs del pipeline
        # ---------------------------
        print("  ⚙️  pipeline_jobs...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.pipeline_jobs (
                job_id      UUID PRIMARY KEY,   -- Identificador único del job generado por el backend
                type        TEXT,               -- Tipo de job: "full", "sync", "embeddings", etc.
                status      TEXT,               -- Estado: "running", "completed", "error"
                result      JSONB,              -- Resultado del job en JSON (output de cada paso)
                error       TEXT,               -- Mensaje de error si el job falló
                started_at  TIMESTAMP,
                finished_at TIMESTAMP
            );
        """)

    conn.commit()  # Confirma todas las creaciones de tabla en una sola transacción


# ---------------------------
# Insertar modelos de IA iniciales (solo si no existen)
# ---------------------------
def insert_initial_models(conn) -> None:
    models = [
        ("GPT-4o Mini",  "gpt-4o-mini",  "Modelo rápido y económico. Valor por defecto para todos los prompts."),
        ("GPT-4o",       "gpt-4o",       "Modelo equilibrado. Buena relación calidad-velocidad para clasificación e intención."),
        ("GPT-4.1",      "gpt-4.1",      "Alta capacidad de razonamiento. Recomendado para intención y clasificación complejas."),
        ("GPT-4.1 Mini", "gpt-4.1-mini", "Versión ligera de GPT-4.1. Buen equilibrio entre velocidad y calidad."),
        ("GPT-5.4",      "gpt-5.4",      "Modelo más avanzado disponible. Máxima precisión en análisis complejos."),
        ("GPT-5.4 Mini", "gpt-5.4-mini", "Versión eficiente de GPT-5.4. Recomendado para asignación de tags."),
    ]
    with conn.cursor() as cur:
        for name, deployment, description in models:
            cur.execute("""
                INSERT INTO public.ado_config_models (name, deployment, description, active)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (deployment) DO NOTHING;
            """, (name, deployment, description))
            if cur.rowcount > 0:
                print(f"  ⚙️  Modelo '{name}' ({deployment}) insertado.")
            else:
                print(f"  ⏭️  Modelo '{deployment}' ya existe, omitido.")
    conn.commit()


# ---------------------------
# Asignar modelo por defecto a prompts sin modelo asociado
# ---------------------------
def assign_default_model_to_prompts(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE public.ado_config_prompt
            SET model_id = (SELECT id FROM public.ado_config_models WHERE deployment = 'gpt-4o-mini')
            WHERE model_id IS NULL
        """)
        updated = cur.rowcount
    conn.commit()
    if updated > 0:
        print(f"  ⚙️  {updated} versiones de prompt asociadas al modelo por defecto (gpt-4o-mini).")
    else:
        print(f"  ⏭️  Todos los prompts ya tienen modelo asociado.")


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

    print("🤖 Insertando modelos de IA...")
    insert_initial_models(conn)

    insert_initial_prompts(conn)

    print("🔗 Asignando modelo por defecto a prompts existentes...")
    assign_default_model_to_prompts(conn)

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
            """, (name, text, name))  # WHERE NOT EXISTS garantiza que solo inserta si el prompt no existe todavía
            if cur.rowcount > 0:
                print(f"  ⚙️  Prompt '{name}' insertado.")
            else:
                print(f"  ⏭️  Prompt '{name}' ya existe, omitido.")
    conn.commit()


if __name__ == "__main__":
    main()
