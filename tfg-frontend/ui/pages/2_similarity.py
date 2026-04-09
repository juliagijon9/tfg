import os
import requests
import streamlit as st
import pandas as pd

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Buscar Similares", page_icon="🔍", layout="wide")

st.title("🔍 Buscar Tickets Similares")

# ----- Input -----

col1, col2 = st.columns([2, 1])
with col1:
    source_id = st.number_input("Ticket ID", min_value=1, step=1, value=174132)
with col2:
    top_k = st.slider("Top K", min_value=1, max_value=50, value=10)

# ----- Find Similar -----

if st.button("🔍 Buscar Similares", key="find"):
    with st.spinner("Buscando..."):
        try:
            r = requests.post(
                f"{BACKEND_URL}/similarity/find",
                json={"source_id": int(source_id), "top_k": top_k},
                timeout=30,
            )
            if r.status_code == 404:
                st.warning(r.json().get("detail", "No encontrado"))
            else:
                r.raise_for_status()
                data = r.json()
                results = data.get("results", [])

                if not results:
                    st.info("No se encontraron tickets similares.")
                else:
                    st.success(f"✅ {len(results)} tickets similares encontrados")

                    df = pd.DataFrame(results)
                    df = df.rename(columns={
                        "work_item_id": "ID",
                        "title": "Título",
                        "score": "Similitud",
                        "relation_type": "Tipo Relación",
                    })
                    st.dataframe(df, use_container_width=True, hide_index=True)

                    # Store in session for saving
                    st.session_state["last_source_id"] = int(source_id)
                    st.session_state["last_results"] = results
        except requests.RequestException as e:
            st.error(f"Error: {e}")

st.markdown("---")

# ----- Save Relations -----

st.subheader("💾 Guardar Relaciones")

if "last_results" in st.session_state and st.session_state["last_results"]:
    st.caption(
        f"Guardar relaciones para ticket #{st.session_state['last_source_id']} "
        f"({len([r for r in st.session_state['last_results'] if r.get('relation_type')])} con tipo asignado)"
    )

    if st.button("💾 Guardar en BD", key="save"):
        with st.spinner("Guardando..."):
            try:
                relations = [
                    {
                        "target_id": r["work_item_id"],
                        "relation_type": r["relation_type"],
                        "similarity": r["score"],
                    }
                    for r in st.session_state["last_results"]
                    if r.get("relation_type")
                ]

                if not relations:
                    st.warning("No hay relaciones con tipo asignado (score < umbral related).")
                else:
                    r = requests.post(
                        f"{BACKEND_URL}/relations/save",
                        json={
                            "source_id": st.session_state["last_source_id"],
                            "relations": relations,
                        },
                        timeout=10,
                    )
                    r.raise_for_status()
                    result = r.json()
                    st.success(f"✅ {result.get('saved', 0)} relaciones guardadas")
            except requests.RequestException as e:
                st.error(f"Error al guardar: {e}")
else:
    st.caption("Primero busca tickets similares arriba.")
