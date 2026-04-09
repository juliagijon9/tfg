import os
import time
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Sync & Embeddings", page_icon="🔄", layout="wide")

st.title("🔄 Sincronización y Embeddings")


def poll_job(job_id: str, label: str):
    """Poll a background job until it finishes."""
    progress = st.empty()
    while True:
        r = requests.get(f"{BACKEND_URL}/jobs/{job_id}", timeout=10)
        job = r.json()
        status = job.get("status")

        if status == "completed":
            progress.empty()
            st.success(f"✅ {label} completado")
            if job.get("result"):
                st.json(job["result"])
            return
        elif status == "failed":
            progress.empty()
            st.error(f"❌ {label} falló: {job.get('error')}")
            return
        else:
            progress.info(f"⏳ {label} en progreso... (estado: {status})")
            time.sleep(2)


# ----- Sync ADO -----

st.subheader("Sincronizar desde Azure DevOps")
st.caption("Descarga work items de ADO y los guarda/actualiza en PostgreSQL.")

if st.button("🔄 Sync ADO", key="sync"):
    with st.spinner("Lanzando sincronización..."):
        try:
            r = requests.post(f"{BACKEND_URL}/jobs/sync", timeout=10)
            r.raise_for_status()
            job_id = r.json()["job_id"]
            poll_job(job_id, "Sincronización ADO")
        except requests.RequestException as e:
            st.error(f"Error al lanzar sync: {e}")

st.markdown("---")

# ----- Generate Embeddings -----

st.subheader("Generar Embeddings")
st.caption("Genera embeddings para todos los work items que no los tengan aún. Usa Azure OpenAI si está configurado, o modo dummy como fallback.")

if st.button("🧠 Generate Embeddings", key="embeddings"):
    with st.spinner("Lanzando generación de embeddings..."):
        try:
            r = requests.post(f"{BACKEND_URL}/jobs/embeddings", timeout=10)
            r.raise_for_status()
            job_id = r.json()["job_id"]
            poll_job(job_id, "Generación de embeddings")
        except requests.RequestException as e:
            st.error(f"Error al lanzar embeddings: {e}")
