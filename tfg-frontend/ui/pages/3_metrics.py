import os
import requests
import streamlit as st
import pandas as pd

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Métricas", page_icon="📊", layout="wide")

st.title("📊 Métricas de Relaciones")

# Fetch metrics
try:
    r = requests.get(f"{BACKEND_URL}/metrics", timeout=10)
    r.raise_for_status()
    data = r.json()
except requests.RequestException as e:
    st.error(f"Error al obtener métricas: {e}")
    st.stop()

if data.get("total_relations", 0) == 0:
    st.warning("No hay relaciones. Ejecuta el proceso de linking primero.")
    st.stop()

# ----- KPI Cards -----

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Relaciones", f"{data['total_relations']:,}")
with col2:
    coverage = data.get("coverage", {})
    st.metric("Cobertura", f"{coverage.get('pct', 0):.1f}%",
              help=f"{coverage.get('with_relations', 0)} de {coverage.get('total', 0)} tickets")
with col3:
    stats = data.get("stats", {})
    st.metric("Similitud Media", f"{stats.get('avg', 0):.4f}")
with col4:
    st.metric("Similitud Mediana", f"{stats.get('median', 0):.4f}")

st.markdown("---")

# ----- Distribution by type -----

col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Distribución por tipo")
    by_type = data.get("by_type", [])
    if by_type:
        df_type = pd.DataFrame(by_type)
        st.dataframe(df_type, use_container_width=True, hide_index=True)

with col_right:
    st.subheader("Histograma de similitud")
    histogram = data.get("histogram", [])
    if histogram:
        df_hist = pd.DataFrame(histogram)
        st.bar_chart(df_hist.set_index("bucket")["count"])

st.markdown("---")

# ----- Top pairs -----

st.subheader("Top 5 pares más similares")
top_pairs = data.get("top_pairs", [])
if top_pairs:
    df_pairs = pd.DataFrame(top_pairs)
    st.dataframe(df_pairs, use_container_width=True, hide_index=True)

st.markdown("---")

# ----- Hubs -----

col_hubs, col_wit = st.columns(2)

with col_hubs:
    st.subheader("Top 5 tickets hub")
    hubs = data.get("hubs", [])
    if hubs:
        df_hubs = pd.DataFrame(hubs)
        st.dataframe(df_hubs, use_container_width=True, hide_index=True)

with col_wit:
    st.subheader("Relaciones por tipo de work item")
    by_wit = data.get("by_work_item_type", [])
    if by_wit:
        df_wit = pd.DataFrame(by_wit)
        st.dataframe(df_wit, use_container_width=True, hide_index=True)

# ----- Stats detail -----

st.markdown("---")
st.subheader("Estadísticas de similitud")
stats = data.get("stats", {})
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Mín", f"{stats.get('min', 0):.4f}")
with col2:
    st.metric("Máx", f"{stats.get('max', 0):.4f}")
with col3:
    st.metric("Media", f"{stats.get('avg', 0):.4f}")
with col4:
    st.metric("Mediana", f"{stats.get('median', 0):.4f}")
