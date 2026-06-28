# Sistema de Triaje Inteligente de Tickets — Iberia Express

Sistema que automatiza el análisis inicial (triaje) de tickets de Azure DevOps mediante inteligencia artificial generativa. El sistema sincroniza los tickets, extrae su intención, los clasifica en un área funcional, detecta duplicados o relacionados y les asigna etiquetas descriptivas, todo ello sin intervención manual.

Proyecto desarrollado como Trabajo Fin de Grado en Ingeniería Telemática, implementado en el departamento de IT de Iberia Express.

## Arquitectura

El sistema se compone de cuatro servicios desplegados con Docker Compose:

| Servicio | Tecnología | Puerto |
|---|---|---|
| `tfg-frontend` | Streamlit | 8501 |
| `tfg-backend` | FastAPI | 8000 |
| `tfg-database` | PostgreSQL 16 | 5432 |
| `pgadmin` | pgAdmin 4 | 5050 |

```
Azure DevOps ──► sync_ado_to_postgres.py ──► PostgreSQL ──► pipeline de IA (6 pasos) ──► Backend (FastAPI) ──► Frontend (Streamlit)
                                                                                                  │
                                                                                          Azure OpenAI (LLM + embeddings)
```

## Pipeline de inteligencia artificial

Seis scripts ejecutados de forma secuencial e incremental (solo procesan tickets pendientes):

1. **`sync_ado_to_postgres.py`** — sincroniza tickets nuevos o modificados desde Azure DevOps.
2. **`generate_embeddings.py`** — genera el vector semántico (embedding) de cada ticket.
3. **`link_related.py`** — detecta tickets duplicados (similitud ≥ 0,90) o relacionados (≥ 0,80) por similitud coseno.
4. **`extract_intention.py`** — extrae la intención del ticket y un nivel de confianza (1–4).
5. **`classify_tickets.py`** — clasifica el ticket en un área funcional, apoyándose en un catálogo de ejemplos validados por expertos (Gold Standard).
6. **`tag_tickets.py`** — asigna etiquetas funcionales (tags) con justificación individual.

Los modelos de IA (Azure OpenAI: GPT-4o, GPT-4o Mini, GPT-4.1, GPT-4.1 Mini, GPT-5.4, GPT-5.4 Mini) y los prompts utilizados en cada paso son configurables desde la interfaz web, con versionado automático y sin necesidad de modificar el código.

## Backend — API REST (FastAPI)

Expone los endpoints para controlar el pipeline, consultar el triaje de los tickets y gestionar la configuración de IA. Documentación interactiva disponible en `http://localhost:8000/docs`.

Grupos principales de endpoints:
- **Pipeline**: lanzar pasos individuales o el pipeline completo, consultar estado de los trabajos (`jobs`).
- **Tickets**: consulta de datos, triaje completo, duplicados/relacionados, recálculo de IA.
- **Config**: gestión de prompts versionados, modelos de IA y catálogo de referencia (Gold Standard).
- **Stats**: estadísticas agregadas filtradas por fecha.

## Frontend — Interfaz web (Streamlit)

Páginas disponibles en `tfg-frontend/ui/pages/`:

| Página | Función |
|---|---|
| `1_pipeline.py` | Lanzar y supervisar el pipeline de IA |
| `2_modelos_ia.py` | Gestión de los modelos de IA configurables |
| `3_prompts.py` | Edición y versionado de prompts |
| `4_modelos_clasificacion.py` | Gestión del catálogo de referencia (Gold Standard) |
| `5_tickets.py` | Búsqueda y consulta del triaje de un ticket |
| `6_estadisticas.py` | Estadísticas del sistema filtradas por fecha |

## Base de datos

PostgreSQL 16 con un esquema relacional que almacena: tickets sincronizados, embeddings, relaciones (duplicados/relacionados), intenciones, clasificaciones, tags, prompts versionados, modelos de IA, catálogo de referencia (Gold Standard) e historial de ejecuciones del pipeline.

## Requisitos previos

- Docker y Docker Compose
- Una organización y proyecto de Azure DevOps con un Personal Access Token (PAT)
- Una instancia de Azure OpenAI con los deployments de chat y embeddings configurados

> **⚠️ Nota de seguridad:** por motivos de seguridad, este repositorio **no incluye**:
> - Las credenciales reales de Azure DevOps ni de Azure OpenAI (debes generarlas tú mismo y completarlas en tu propio `.env`, ver `.env.example`).
> - El texto completo de los prompts de producción (intención, clasificación y tags), ya que contienen información interna del negocio de la empresa (nombres de equipos, sistemas internos, reglas de clasificación). El sistema funciona igualmente sin ellos: basta con crear tus propios prompts desde la interfaz web (página **Prompts**) la primera vez que se ejecute `create_tables.py`, o insertarlos manualmente en la tabla `ado_config_prompt`.

## Puesta en marcha

1. Copia el fichero de variables de entorno y complétalo con tus credenciales:

   ```bash
   cp .env.example .env
   ```

   Variables principales a configurar en `.env`:
   - `POSTGRES_*` — credenciales de la base de datos
   - `PGADMIN_*` — credenciales de acceso a pgAdmin
   - `ADO_ORG`, `ADO_PROJECT`, `ADO_PAT` — acceso a Azure DevOps
   - `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT` — acceso a Azure OpenAI (si se deja vacío, se generan embeddings deterministas de prueba con `EMBEDDING_MODEL=dummy`)
   - `RELATED_THRESHOLD`, `DUPLICATE_THRESHOLD`, `TOP_K` — umbrales de similitud para la detección de duplicados/relacionados

2. Levanta los contenedores:

   ```bash
   docker compose up -d
   ```

3. Inicializa la base de datos (crea las tablas y los prompts iniciales):

   ```bash
   docker compose exec backend python scripts/create_tables.py
   ```

4. Accede a la interfaz web en [http://localhost:8501](http://localhost:8501) y lanza el pipeline desde la página **Pipeline**, o ejecútalo manualmente:

   ```bash
   docker compose exec backend python scripts/sync_ado_to_postgres.py
   docker compose exec backend python scripts/generate_embeddings.py
   docker compose exec backend python scripts/link_related.py
   docker compose exec backend python scripts/extract_intention.py
   docker compose exec backend python scripts/classify_tickets.py
   docker compose exec backend python scripts/tag_tickets.py
   ```

## Accesos

| Servicio | URL |
|---|---|
| Interfaz web | http://localhost:8501 |
| API / documentación (Swagger) | http://localhost:8000/docs |
| pgAdmin | http://localhost:5050 |

## Estructura del repositorio

```
.
├── docker-compose.yml
├── .env.example
├── scripts/                      # Pipeline de IA (6 scripts)
│   ├── create_tables.py
│   ├── sync_ado_to_postgres.py
│   ├── generate_embeddings.py
│   ├── link_related.py
│   ├── extract_intention.py
│   ├── classify_tickets.py
│   └── tag_tickets.py
├── tfg-backend/                  # API REST (FastAPI)
│   └── backend/
│       ├── api.py
│       ├── config.py
│       ├── db.py
│       ├── jobs.py
│       └── models.py
├── tfg-frontend/                 # Interfaz web (Streamlit)
│   └── ui/
│       ├── app.py
│       └── pages/
└── tfg-db/                       # Inicialización de PostgreSQL
    └── init.sql
```
