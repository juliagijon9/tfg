import os
import time
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Pipeline", page_icon="🔄", layout="wide")
st.title("🔄 Pipeline de Triaje")
st.caption("Ejecuta los pasos del pipeline individualmente o todos en orden.")

STEPS = [
    ("sync",               "🔁 Sincronizar ADO → BD",       "Descarga los tickets nuevos o modificados de Azure DevOps y los guarda en PostgreSQL."),
    ("embeddings",         "🧮 Generar Embeddings",          "Genera vectores semánticos para los tickets que aún no tienen embedding."),
    ("link-related",       "🔗 Detectar Relacionados",       "Calcula similitud entre tickets y guarda duplicados y relacionados."),
    ("extract-intention",  "🧠 Extraer Intención",           "Usa el LLM para clarificar la intención real de cada ticket."),
    ("classify",           "🏷️ Clasificar",                  "Asigna cada ticket a un área funcional usando el LLM."),
    ("tag",                "🔖 Asignar Tags",                "Propone tags funcionales y técnicos para cada ticket usando el LLM."),
]


def poll_job(job_id: str, label: str):
    placeholder = st.empty()
    while True:
        try:
            r = requests.get(f"{BACKEND_URL}/jobs/{job_id}", timeout=10)
            job = r.json()
        except Exception as e:
            placeholder.error(f"Error consultando job: {e}")
            return

        status = job.get("status")

        if status == "completed":
            placeholder.empty()
            steps = job.get("result", {}).get("steps", [])
            for s in steps:
                output = s.get("output", "").strip()
                if output:
                    st.code(output, language=None)
            st.success(f"✅ {label} completado")
            return

        elif status == "failed":
            placeholder.empty()
            st.error(f"❌ {label} falló: {job.get('error', '')}")
            steps = job.get("result", {}).get("steps", [])
            for s in steps:
                if not s.get("ok"):
                    st.code(s.get("output", ""), language=None)
            return

        else:
            placeholder.info(f"⏳ {label} en progreso…")
            time.sleep(2)


st.markdown("---")

# --- Pasos individuales ---
st.subheader("Pasos individuales")

for step_id, label, description in STEPS:
    with st.expander(label):
        st.caption(description)
        if st.button(f"Ejecutar: {label}", key=f"btn_{step_id}"):
            try:
                r = requests.post(f"{BACKEND_URL}/pipeline/{step_id}", timeout=10)
                r.raise_for_status()
                job_id = r.json()["job_id"]
                poll_job(job_id, label)
            except requests.RequestException as e:
                st.error(f"Error al lanzar el paso: {e}")

st.markdown("---")

# --- Ejecutar todo ---
st.subheader("Ejecutar pipeline completo")
st.caption("Lanza los 6 pasos en orden. Si uno falla, el proceso se detiene.")

if st.button("🚀 Ejecutar todo el pipeline", type="primary"):
    try:
        r = requests.post(f"{BACKEND_URL}/pipeline/run-all", timeout=10)
        r.raise_for_status()
        job_id = r.json()["job_id"]
        poll_job(job_id, "Pipeline completo")
    except requests.RequestException as e:
        st.error(f"Error al lanzar el pipeline: {e}")
