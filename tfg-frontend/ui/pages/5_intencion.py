import os
import time
from datetime import date

import pandas as pd
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Extracción de Intención", page_icon="🧠", layout="wide")

st.title("🧠 Extracción de Intención")
st.caption(
    "Analiza los tickets de Azure DevOps almacenados en la base de datos y "
    "clarifica su intención real usando IA, traduciendo el lenguaje del usuario "
    "en una formulación técnica y concreta."
)

st.markdown("---")

col1, col2 = st.columns(2)
with col1:
    since = st.date_input(
        "Tickets creados desde",
        value=date(2026, 4, 30),
        help="Se procesarán los tickets con fecha de creación posterior a esta fecha.",
    )
with col2:
    limit = st.slider("Número máximo de tickets", min_value=1, max_value=50, value=10)

if st.button("🧠 Extraer intención", type="primary"):
    since_str = since.strftime("%Y-%m-%d")

    with st.spinner(f"Lanzando extracción para tickets desde {since_str}…"):
        try:
            r = requests.post(
                f"{BACKEND_URL}/jobs/extract-intention",
                params={"since": since_str, "limit": limit},
                timeout=10,
            )
            r.raise_for_status()
            job_id = r.json()["job_id"]
        except requests.RequestException as e:
            st.error(f"Error al lanzar la extracción: {e}")
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
                st.success(f"✅ {len(items)} ticket(s) procesados correctamente.")
                df = pd.DataFrame(items, columns=["id", "work_item_type", "title", "intention"])
                df.columns = ["ID", "Tipo", "Título", "Intención"]
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "ID": st.column_config.NumberColumn(width="small"),
                        "Tipo": st.column_config.TextColumn(width="small"),
                        "Título": st.column_config.TextColumn(width="medium"),
                        "Intención": st.column_config.TextColumn(width="large"),
                    },
                )
            break

        elif status == "failed":
            progress_placeholder.empty()
            st.error(f"❌ La extracción falló: {job.get('error')}")
            break

        else:
            progress_placeholder.info(f"⏳ Procesando tickets… (estado: {status})")
            time.sleep(2)
