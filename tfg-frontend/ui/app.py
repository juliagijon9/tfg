import os
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="TFG - Asistente de Triaje", page_icon="🎯", layout="wide")

st.title("🎯 Asistente de Triaje Inteligente")
st.caption("Azure DevOps — Detección de tickets similares y duplicados")

st.markdown("---")

# Quick status
col1, col2, col3 = st.columns(3)

with col1:
    try:
        r = requests.get(f"{BACKEND_URL}/health", timeout=5)
        data = r.json()
        if data.get("db_connected"):
            st.metric("Base de datos", "✅ Conectada")
        else:
            st.metric("Base de datos", "❌ Sin conexión")
    except Exception:
        st.metric("Base de datos", "❌ Sin conexión")

with col2:
    try:
        r = requests.get(f"{BACKEND_URL}/work-items/count", timeout=5)
        count = r.json().get("count", 0)
        st.metric("Work Items", f"{count:,}")
    except Exception:
        st.metric("Work Items", "—")

with col3:
    try:
        r = requests.get(f"{BACKEND_URL}/metrics", timeout=5)
        data = r.json()
        total_rel = data.get("total_relations", 0)
        st.metric("Relaciones", f"{total_rel:,}")
    except Exception:
        st.metric("Relaciones", "—")

st.markdown("---")
st.info("Usa el menú lateral para navegar entre las páginas: **Sync & Embeddings**, **Buscar Similares**, **Métricas**.")