# Sistema de triaje inteligente de tickets

Aplicación para sincronizar work items de Azure DevOps, analizarlos con Azure OpenAI, guardar resultados en PostgreSQL y consultarlos desde una interfaz web. Este README explica cómo instalarlo en un ordenador nuevo, configurarlo, iniciarlo, operarlo y trasladar datos de una instalación anterior.

## 1. Componentes y flujo

El proyecto usa Docker Compose; no hace falta instalar Python en el host.

| Servicio | Función | Puerto local |
|---|---|---:|
| frontend | Interfaz Streamlit | 8501 |
| backend | API FastAPI y ejecución de tareas | 8000 |
| database | PostgreSQL 16 | 5432 |
| pgadmin | Administración de PostgreSQL | 5050 |
| scheduler | Inicia el pipeline en los horarios configurados | No publica puerto |

Flujo del pipeline completo:

1. Sincronización de Azure DevOps a PostgreSQL.
2. Generación de embeddings para tickets pendientes.
3. Cálculo de relaciones y duplicados.
4. Extracción de intención.
5. Clasificación por área funcional.
6. Asignación de tags.

La base de datos se guarda en el volumen Docker `db_data`; los archivos del repositorio no contienen una copia de los tickets. La configuración de pgAdmin se guarda en `pgadmin_data`.

## 2. Requisitos

En el ordenador nuevo se necesita:

- Git para descargar el repositorio.
- Docker Desktop actualizado (Windows/macOS) o Docker Engine más el complemento Docker Compose v2 (Linux).
- Conexión a internet para descargar imágenes y contactar con Azure.
- Acceso a la organización/proyecto de Azure DevOps.
- Acceso a un recurso Azure OpenAI/Foundry con deployments de chat y embeddings ya creados.
- Permiso para enviar a Azure OpenAI el contenido de los tickets que analiza el pipeline.

Comprueba que Docker responde:

    docker --version
    docker compose version

En Windows, ejecuta los comandos siguientes en PowerShell o en una terminal con Docker disponible. Los comandos de esta guía usan el formato Docker Compose v2: `docker compose`, con espacio.

## 3. Credenciales y recursos de Azure

Antes de arrancar, prepara credenciales propias para el nuevo entorno. No copies las credenciales de otra persona ni las añadas al repositorio.

### Azure DevOps

Crea un PAT con el alcance mínimo que permita leer los work items del proyecto. El usuario asociado al PAT también debe tener acceso al proyecto. El PAT puede caducar o revocarse; renuévalo y actualiza el fichero local `.env` cuando corresponda.

El proyecto conserva dos nombres para las credenciales de DevOps y hay que completar ambos:

- `ADO_ORG`, `ADO_PROJECT`, `ADO_PAT`: los usan la sincronización del pipeline y el servicio de sincronización.
- `AZURE_DEVOPS_ORG_URL`, `AZURE_DEVOPS_PROJECT`, `AZURE_DEVOPS_PAT`: los exige la configuración del backend.

Usa el nombre corto de la organización en `ADO_ORG` (no la URL completa); la URL completa va en `AZURE_DEVOPS_ORG_URL`, normalmente con forma `https://dev.azure.com/mi-organizacion`. El nombre del proyecto debe coincidir exactamente en ambos campos. La sincronización actual filtra tipos de work item concretos: Bug, Feature, Product Backlog Item, Task y Delivery.

### Azure OpenAI

Necesitas:

- Endpoint del recurso, por ejemplo `https://mi-recurso.openai.azure.com/`.
- Una API key del recurso.
- Una API version aceptada por el endpoint.
- Un deployment de embeddings compatible con el código. El valor habitual de este proyecto es `text-embedding-3-large`; el esquema guarda embeddings de 3072 dimensiones, así que no cambies a otro modelo/dimensión sin revisar el código y migrar datos.
- Uno o varios deployments de chat. El nombre configurado en Azure debe coincidir exactamente con el campo `deployment` del catálogo de modelos de la aplicación.

Los modelos que aparecen en la base de datos son entradas de catálogo: **no crean deployments en Azure**. Por ejemplo, activar `gpt-6-luna` en la interfaz solo funciona si ese deployment existe en el recurso Azure indicado.

El modelo usado en cada tarea se selecciona en la última versión de su prompt en la página **Prompts**. `AZURE_OPENAI_DEPLOYMENT` es el fallback del backend/scripts cuando un prompt no tiene modelo asociado. `AZURE_OPENAI_CHAT_DEPLOYMENT` aparece en el fichero de ejemplo por compatibilidad, pero el código actual selecciona el chat por prompt o usa el fallback anterior.

Los tickets pueden contener datos internos. El pipeline envía texto del ticket a Azure OpenAI para análisis; valida autorización, políticas de privacidad, residencia y costes de tu organización antes de procesar datos reales.

## 4. Descargar y preparar el proyecto

Clona el repositorio en una carpeta local. Sustituye la URL por la URL autorizada de tu repositorio:

    git clone <URL_DEL_REPOSITORIO>
    cd tfg

Crea el fichero local de configuración a partir de la plantilla:

    cp .env.example .env

En PowerShell:

    Copy-Item .env.example .env

Edita `.env` y sustituye todos los valores de ejemplo por los de tu entorno. No dejes valores como `your-org`, `changeme` o `your-azure-openai-key`.

### Variables que hay que revisar en .env

| Variable | Valor |
|---|---|
| `POSTGRES_HOST` | Déjalo como `database` para los contenedores de Compose. No uses `localhost` entre contenedores. |
| `POSTGRES_PORT` | Normalmente `5432`. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Base y credenciales locales. Usa una contraseña única y robusta. |
| `PGADMIN_DEFAULT_EMAIL`, `PGADMIN_DEFAULT_PASSWORD` | Usuario y contraseña propios de pgAdmin. No reutilices los valores de ejemplo. |
| `ADO_ORG`, `ADO_PROJECT`, `ADO_PAT` | Nombre corto de organización, nombre exacto de proyecto y PAT. |
| `AZURE_DEVOPS_ORG_URL`, `AZURE_DEVOPS_PROJECT`, `AZURE_DEVOPS_PAT` | URL completa, proyecto y el mismo PAT o uno equivalente. |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION` | Endpoint, clave y versión API admitida por tu recurso. La configuración probada en el entorno del proyecto usa `2024-02-15-preview`; confirma que está disponible para tus deployments. |
| `AZURE_OPENAI_DEPLOYMENT` | Deployment de chat de reserva. Debe existir en Azure. |
| `AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT` | Nombre exacto del deployment de embeddings. |
| `BACKEND_URL` | Déjalo como `http://backend:8000`; es la dirección interna entre contenedores. |

En el fichero de ejemplo también aparece `AZURE_OPENAI_CHAT_DEPLOYMENT`. No la uses como única configuración de chat: actualmente la aplicación se guía por los modelos asociados a cada prompt y por `AZURE_OPENAI_DEPLOYMENT` como fallback.

En Compose, `database` es el nombre DNS interno del servicio. Por eso backend y scripts ejecutados dentro del contenedor conectan a `database:5432`; desde el host, pgAdmin o herramientas locales pueden conectar a `localhost:5432`.

Comprueba que Compose puede interpretar la configuración:

    docker compose config --quiet

Este comando puede advertir de variables obligatorias no definidas: vuelve a revisar `.env`. No publiques la salida de `docker compose config`, porque podría incluir valores sensibles.

## 5. Primer arranque

Para evitar una ejecución prematura, inicia base de datos, backend, interfaz y pgAdmin, pero deja el scheduler apagado hasta configurar el sistema:

    docker compose up -d --build database backend frontend pgadmin

Espera a que PostgreSQL acepte conexiones:

    docker compose exec database sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

Si todavía no está listo, espera unos segundos y repite. Inicializa las tablas y datos de catálogo:

    docker compose exec -T backend python /scripts/create_tables.py

El script crea las tablas necesarias, registra modelos iniciales y crea nombres de prompts iniciales si no existen. Es idempotente para esos objetos y no debe borrar los datos existentes. Los prompts de negocio no se incluyen en el repositorio: los textos iniciales son marcadores, no instrucciones de producción. Debes sustituirlos.

Abre la aplicación:

- Interfaz: http://localhost:8501
- API y Swagger: http://localhost:8000/docs
- Estado backend/base de datos: http://localhost:8000/health
- pgAdmin: http://localhost:5050

La respuesta de salud esperada contiene `"status": "ok"` y `"db_connected": true`.

Si accedes desde otro equipo de la misma red, puedes abrir http://IP_DEL_HOST:8501 si el firewall permite ese puerto. La aplicación no tiene autenticación: limítalo a una red de confianza y no lo publiques directamente en internet.
## 6. Configuración inicial en la interfaz

Antes de ejecutar el pipeline:

1. En **Modelos de IA**, comprueba que cada modelo que vas a usar tiene un deployment real en Azure y que el nombre coincide exactamente. Activa solo los disponibles. Los modelos GPT-6 se siembran inactivos para evitar seleccionarlos por accidente.
2. En **Prompts**, reemplaza los textos marcadores por instrucciones de negocio válidas y guarda una nueva versión para cada tarea:
   - Extracción de intención: define las claves JSON que consume el código, como `intention`, `nivel_confianza` y `nivel_confianza_justificacion`.
   - Clasificación: pide JSON con `area` y `justification`; asegúrate de que las áreas posibles estén claras.
   - Tags: pide JSON con una lista `tags` y los campos que espera el código, `tag` y `justificacion`.
3. Selecciona el modelo para cada versión del prompt. La última versión es la que usan los scripts. Si pretendes usar GPT-6, confirma primero que el deployment funciona y que está asociado a los tres prompts que quieres ejecutar.
4. En **Modelos de clasificación**, carga ejemplos validados de cada área. Este catálogo Gold Standard no se llena automáticamente; sin datos de referencia, la clasificación puede ser poco útil o fallar.
5. Revisa que el endpoint/key de Azure OpenAI y los dos juegos de credenciales DevOps correspondan al mismo entorno previsto.

No uses prompts genéricos para datos reales: la clasificación depende de las áreas y reglas de cada organización.

## 7. Primera prueba y puesta en marcha del scheduler

Haz la primera prueba manual desde **Pipeline** en la interfaz. El pipeline completo llama a Azure DevOps y Azure OpenAI, procesa datos y puede generar costes. Comprueba cada paso y el detalle del job antes de dejarlo automatizado.

También puedes verificar la conexión de Azure DevOps ejecutando solo la sincronización:

    docker compose exec -T backend python /scripts/sync_ado_to_postgres.py

Esto descarga/actualiza work items y los guarda en PostgreSQL; no ejecuta las tareas de IA. Para ejecutar manualmente todos los pasos, usa el botón de pipeline completo en la interfaz. El API responde con un identificador de job y el estado puede consultarse en la página Pipeline o en `/jobs`.

Cuando la configuración y la prueba estén correctas, inicia el scheduler:

    docker compose up -d scheduler

La configuración actual lo ejecuta todos los días a las **08:00 y 14:00, hora de Europe/Madrid**. No es un cron del host: el contenedor scheduler permanece activo y solicita al backend iniciar el pipeline. Los horarios están en `docker-compose.yml`, en `PIPELINE_SCHEDULE_TIMES`; para cambiarlos, edita esa lista en formato HH:MM y recrea el servicio:

    docker compose up -d --force-recreate scheduler

El pipeline es incremental: sincroniza tickets nuevos o modificados y normalmente las tareas posteriores procesan registros pendientes. Aun así, cada ejecución completa consulta DevOps, revisa pendientes y puede llamar a Azure.

## 8. Operación diaria

Comandos habituales:

    docker compose ps
    docker compose logs -f backend
    docker compose logs -f scheduler
    docker compose logs -f database
    docker compose restart backend frontend scheduler
    docker compose pull
    docker compose up -d --build

`docker compose down` detiene y elimina contenedores, pero conserva los volúmenes de base de datos. **No ejecutes `docker compose down -v` salvo que quieras borrar permanentemente los datos de PostgreSQL y pgAdmin.**

Al actualizar el repositorio, haz una copia de seguridad, trae los cambios y reconstruye:

    git pull
    docker compose up -d --build

Si cambió el esquema, revisa las notas de la versión y ejecuta la migración indicada. `create_tables.py` crea tablas/columnas faltantes, pero no sustituye una estrategia de migración para cualquier cambio futuro.

## 9. Copia de seguridad y traslado a otro ordenador

El repositorio por sí solo no contiene los tickets, resultados de IA ni configuración guardados en PostgreSQL. Para trasladar un entorno existente, crea una copia lógica de la base en el ordenador original:

    docker compose exec -T database pg_dump --clean --if-exists -U postgres -d tfg > /ruta/segura/backup-tfg.sql

Si cambiaste `POSTGRES_USER` o `POSTGRES_DB`, usa esos valores en lugar de `postgres` y `tfg`. Guarda el backup fuera del repositorio y en un lugar seguro: contiene work items y datos internos.

El backup completo se restaura en una base de destino con el mismo nombre y usuario PostgreSQL del origen. Guárdalo fuera del repositorio y usa un canal seguro: contiene tickets y configuración. En PowerShell clásico, utiliza WSL/bash o PowerShell 7 para las redirecciones de entrada/salida de estos comandos.

En el ordenador nuevo:

1. Instala el proyecto y configura su propio `.env` con credenciales vigentes.
2. Inicia solo PostgreSQL, sin backend ni scheduler: ejecuta docker compose up -d database.
3. Espera a que PostgreSQL esté listo y confirma la conexión con pg_isready antes de restaurar.
4. Restaura el dump completo. El comando usa --clean --if-exists para reemplazar el esquema inicial de la imagen por el esquema respaldado. **Esto elimina y recrea objetos de la base destino; no lo ejecutes sobre datos que quieras conservar.**

       docker compose exec -T database psql -v ON_ERROR_STOP=1 -U postgres -d tfg < /ruta/segura/backup-tfg.sql

5. Inicia backend, frontend y pgAdmin; comprueba los prompts, modelos y conexiones en la interfaz.
6. Inicia el scheduler cuando la verificación esté terminada.

No copies el volumen Docker entre sistemas como mecanismo de migración. Usa `pg_dump`/restore. No elimines el volumen destino para restaurar sin confirmar antes que no necesitas sus datos.

Para una instalación completamente nueva, no restaures backup: ejecuta `create_tables.py`, configura prompts y Gold Standard y luego sincroniza datos desde Azure DevOps.

## 10. Seguridad

- `.env` está excluido de Git; mantén esa protección. Nunca subas PATs, keys, contraseñas o backups al repositorio ni los pegues en incidencias/logs.
- Usa PATs con caducidad y privilegios mínimos; rótalos si se exponen.
- Cambia todas las contraseñas de ejemplo, especialmente las de PostgreSQL y pgAdmin.
- Los puertos de Compose se publican en todas las interfaces del host. Para uso local, limita acceso con firewall; no expongas PostgreSQL, pgAdmin, API o Streamlit directamente a internet.
- La aplicación no incorpora autenticación de usuario para Streamlit/API. Para acceso multiusuario o remoto, añade autenticación, HTTPS y un proxy/firewall adecuados.
- Protege backups: incluyen datos sincronizados desde DevOps y resultados derivados.
- Antes de procesar tickets, valida con la organización que el contenido puede enviarse al recurso Azure OpenAI seleccionado.

## 11. Resolución de problemas

### Compose informa de variables vacías

Confirma que existe `.env` en la raíz, que copiaste la plantilla y que cambiaste todos los marcadores. Valida con `docker compose config --quiet`. No pegues la configuración resuelta en chats o tickets.

### El backend no llega a PostgreSQL

Dentro de Docker, `POSTGRES_HOST` debe ser `database`, no `localhost`. Revisa `docker compose logs database` y espera a que `pg_isready` responda. Si cambiaste `POSTGRES_PASSWORD` después de inicializar el volumen, PostgreSQL no cambia automáticamente la contraseña almacenada.

### Puerto ocupado

Si 8501, 8000, 5432 o 5050 ya lo usa otra aplicación, cambia el puerto de la izquierda en `docker-compose.yml`. El puerto de la derecha es el interno del contenedor y normalmente se conserva. Actualiza también la URL que abrirás en el navegador.

### Azure DevOps devuelve 401 o 403

Comprueba que el PAT está vigente, tiene el alcance necesario para leer work items y pertenece a un usuario con acceso al proyecto. Revisa que organización y proyecto coinciden en ambos juegos de variables.

### Azure OpenAI devuelve DeploymentNotFound

El nombre del catálogo no crea el deployment. Comprueba en Azure que existe en el recurso del endpoint configurado y que el nombre técnico coincide exactamente con el campo `deployment` del modelo seleccionado por el prompt.

### Un modelo devuelve parámetros no admitidos o no sale JSON

Confirma que ejecutas la versión actual del código y que cada prompt apunta al deployment correcto. Para GPT-6, las llamadas omiten el parámetro temperature y usan reasoning_effort=none, admitido por GPT-6 Luna ([guía oficial GPT-6](https://developers.openai.com/api/docs/guides/latest-model)). El límite max_completion_tokens incluye tokens de razonamiento no visibles, así que deja margen para la respuesta ([guía de tokens](https://developers.openai.com/api/docs/guides/token-counting)). Los demás modelos siguen usando temperature=0. Comprueba la salida detallada de cada paso: un job global completado no siempre significa que cada ticket haya sido procesado sin errores.

### El scheduler no ejecuta a la hora esperada

Consulta `docker compose logs scheduler`. El timezone configurado es `Europe/Madrid` y contempla cambios de horario estacional. El servicio debe permanecer en estado running. Los horarios se editan en `docker-compose.yml`, no en crontab.

## 12. Estructura relevante

    .
    ├── docker-compose.yml
    ├── .env.example
    ├── scripts/
    │   ├── create_tables.py
    │   ├── sync_ado_to_postgres.py
    │   ├── generate_embeddings.py
    │   ├── link_related.py
    │   ├── extract_intention.py
    │   ├── classify_tickets.py
    │   └── tag_tickets.py
    ├── tfg-backend/
    │   └── backend/
    ├── tfg-frontend/
    │   └── ui/pages/
    └── tfg-db/
        └── init.sql
