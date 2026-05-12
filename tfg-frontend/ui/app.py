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

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Tickets", f"{stats.get('tickets', '—'):,}" if stats else "—")
col2.metric("Embeddings", f"{stats.get('embeddings', '—'):,}" if stats else "—")
col3.metric("Intenciones", f"{stats.get('intentions', '—'):,}" if stats else "—")
col4.metric("Clasificados", f"{stats.get('classifications', '—'):,}" if stats else "—")
col5.metric("Tags", f"{stats.get('tags', '—'):,}" if stats else "—")
col6.metric("Relaciones", f"{stats.get('relations', '—'):,}" if stats else "—")

st.markdown("---")

# --- Estado del backend ---
try:
    h = requests.get(f"{BACKEND_URL}/health", timeout=3)
    health = h.json()
    if health.get("db_connected"):
        st.success("✅ Backend y base de datos operativos")
    else:
        st.error("❌ Backend activo pero sin conexión a la BD")
except Exception:
    st.error("❌ No se puede conectar con el backend")

st.markdown("---")
st.info(
    "Usa el menú lateral para navegar:\n\n"
    "🔄 **Pipeline** — Ejecutar los pasos del triaje\n\n"
    "📝 **Prompts** — Gestionar los prompts de los LLMs\n\n"
    "🎫 **Tickets** — Consultar y gestionar tickets\n\n"
    "📊 **Estadísticas** — Informes y distribuciones"
)
