import os
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(
    page_title="Triaje Iberia Express",
    page_icon="✈️",
    layout="wide",
)

st.title("✈️ Asistente de Triaje — Iberia Express")
st.caption("Sistema de triaje inteligente de tickets Azure DevOps")

st.markdown("---")

# --- Métricas principales ---
try:
    r = requests.get(f"{BACKEND_URL}/stats", timeout=5)
    stats = r.json() if r.ok else {}
except Exception:
    stats = {}

col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
col1.metric("Tickets", f"{stats.get('tickets', 0):,}" if stats else "—")
col2.metric("Embeddings", f"{stats.get('embeddings', 0):,}" if stats else "—")
col3.metric("Relaciones", f"{stats.get('relations', 0):,}" if stats else "—")
col4.metric("Intenciones", f"{stats.get('intentions', 0):,}" if stats else "—")
col5.metric("Clasificados", f"{stats.get('classifications', 0):,}" if stats else "—")
col6.metric("Tags", f"{stats.get('tags', 0):,}" if stats else "—")
col7.metric("Último ID", f"{stats.get('max_id', 0):,}" if stats else "—")

st.markdown("---")

# --- Estado del backend y jobs activos ---
try:
    h = requests.get(f"{BACKEND_URL}/health", timeout=3)
    health = h.json()
    db_ok = health.get("db_connected", False)
except Exception:
    db_ok = False

if db_ok:
    st.success("✅ Backend y base de datos operativos")
else:
    st.error("❌ No se puede conectar con el backend")

# --- Jobs activos y recientes ---
STEP_LABELS = {
    "sync": "🔁 Sincronizar ADO → BD",
    "embeddings": "🧮 Generar Embeddings",
    "link-related": "🔗 Detectar Relacionados",
    "extract-intention": "🧠 Extraer Intención",
    "classify": "🏷️ Clasificar",
    "tag": "🔖 Asignar Tags",
    "run-all": "🚀 Pipeline completo",
}

STATUS_BADGE = {
    "completed": "✅",
    "failed": "❌",
    "running": "⏳",
    "pending": "🕐",
}

try:
    rj = requests.get(f"{BACKEND_URL}/jobs?limit=10", timeout=5)
    jobs = rj.json() if rj.ok else []
except Exception:
    jobs = []

running = [j for j in jobs if j["status"] == "running"]

st.markdown("---")

if running:
    for job in running:
        label = STEP_LABELS.get(job["type"], job["type"])
        st.warning(f"⏳ **En curso:** {label} — Job `{job['job_id'][:8]}…`")
else:
    st.info("💤 No hay procesos en ejecución.")

st.markdown("---")
st.subheader("Últimas 10 ejecuciones")
if jobs:
    for job in jobs:
        label = STEP_LABELS.get(job["type"], job["type"])
        badge = STATUS_BADGE.get(job["status"], "❓")
        started = job.get("started_at", "")[:16].replace("T", " ")
        finished = job.get("finished_at", "")
        finished_str = finished[:16].replace("T", " ") if finished else "—"
        error_str = f" — ⚠️ {job['error'][:80]}" if job.get("error") else ""

        result = job.get("result") or {}
        steps = result.get("steps", []) if isinstance(result, dict) else []
        output_text = "\n\n".join(
            s["output"].strip() for s in steps if s.get("output", "").strip()
        )

        header = (
            f"{badge} **{label}** &nbsp;|&nbsp; "
            f"{started} → {finished_str}"
            f"{error_str} &nbsp;|&nbsp; `{job['job_id'][:8]}…`"
        )
        if output_text:
            with st.expander(header):
                st.code(output_text, language=None)
        else:
            st.markdown(header)
else:
    st.caption("Sin ejecuciones registradas.")

st.markdown("---")
st.info(
    "Usa el menú lateral para navegar:\n\n"
    "🔄 **Pipeline** — Ejecutar los pasos del triaje\n\n"
    "📝 **Prompts** — Gestionar los prompts de los LLMs\n\n"
    "🎫 **Tickets** — Consultar y gestionar tickets\n\n"
    "📊 **Estadísticas** — Informes y distribuciones"
)
