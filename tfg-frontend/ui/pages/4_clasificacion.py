import os
import time

import pandas as pd
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Clasificación de Tickets", page_icon="🎯", layout="wide")

st.title("🎯 Clasificación de Tickets")
st.caption(
    "Recupera los tickets más recientes de Azure DevOps y los clasifica "
    "automáticamente con IA en una de las 7 áreas de Iberia Express."
)

st.markdown("---")

top = st.slider("Número de tickets a clasificar", min_value=1, max_value=20, value=5)

if st.button("🚀 Clasificar tickets", type="primary"):
    with st.spinner(f"Lanzando clasificación de {top} tickets…"):
        try:
            r = requests.post(f"{BACKEND_URL}/jobs/classify?top={top}", timeout=10)
            r.raise_for_status()
            job_id = r.json()["job_id"]
        except requests.RequestException as e:
            st.error(f"Error al lanzar la clasificación: {e}")
            st.stop()

    progress_placeholder = st.empty()
    while True:
        try:
            r = requests.get(f"{BACKEND_URL}/jobs/{job_id}", timeout=10)
            job = r.json()
        except requests.RequestException as e:
            st.error(f"Error al consultar el estado del job: {e}")
            break

        status = job.get("status")

        if status == "completed":
            progress_placeholder.empty()
            items = job.get("result", {}).get("items", [])
            if not items:
                st.warning("No se encontraron tickets con los filtros actuales.")
            else:
                st.success(f"✅ {len(items)} ticket(s) clasificados correctamente.")
                df = pd.DataFrame(items, columns=["id", "title", "area", "justification"])
                df.columns = ["ID", "Título", "Área", "Justificación"]
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "ID": st.column_config.NumberColumn(width="small"),
                        "Título": st.column_config.TextColumn(width="large"),
                        "Área": st.column_config.TextColumn(width="medium"),
                        "Justificación": st.column_config.TextColumn(width="large"),
                    },
                )
            break

        elif status == "failed":
            progress_placeholder.empty()
            st.error(f"❌ La clasificación falló: {job.get('error')}")
            break

        else:
            progress_placeholder.info(f"⏳ Clasificando tickets… (estado: {status})")
            time.sleep(2)
