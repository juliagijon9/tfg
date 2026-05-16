import os
import time
from datetime import datetime, timezone

import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
MIN_MINUTES_TO_FORCE_FAIL = 30

st.set_page_config(page_title="Pipeline", page_icon="🔄", layout="wide")
st.title("🔄 Pipeline de Triaje")
st.caption("Ejecuta los pasos del pipeline individualmente o todos en orden.")

STEPS = [
    ("sync",              "🔁 Sincronizar ADO → BD",    "Descarga tickets nuevos o modificados de Azure DevOps."),
    ("embeddings",        "🧮 Generar Embeddings",       "Genera vectores semánticos para tickets sin embedding."),
    ("link-related",      "🔗 Detectar Relacionados",    "Calcula similitud y guarda duplicados y relacionados."),
    ("extract-intention", "🧠 Extraer Intención",        "Clarifica la intención real de cada ticket con el LLM."),
    ("classify",          "🏷️ Clasificar",               "Asigna cada ticket a un área funcional."),
    ("tag",               "🔖 Asignar Tags",             "Propone tags funcionales y técnicos para cada ticket."),
]

STEP_LABELS = {s[0]: s[1] for s in STEPS}


# ---------------------------
# Helpers
# ---------------------------
def get_recent_jobs(limit=10):
    try:
        r = requests.get(f"{BACKEND_URL}/jobs?limit={limit}", timeout=5)
        return r.json() if r.ok else []
    except Exception:
        return []


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
            output_lines = []
            for s in job.get("result", {}).get("steps", []):
                if s.get("output", "").strip():
                    output_lines.append(s["output"].strip())
            combined = "\n\n".join(output_lines)
            st.session_state["last_job_output"] = combined
            st.session_state["last_job_label"] = label
            st.session_state["last_job_ok"] = True
            st.rerun()
            return
        elif status == "failed":
            placeholder.empty()
            output_lines = []
            for s in job.get("result", {}).get("steps", []):
                if not s.get("ok") and s.get("output", "").strip():
                    output_lines.append(s["output"].strip())
            combined = "\n\n".join(output_lines)
            st.session_state["last_job_output"] = combined
            st.session_state["last_job_label"] = label
            st.session_state["last_job_ok"] = False
            st.session_state["last_job_error"] = job.get("error", "")
            st.rerun()
            return
        else:
            placeholder.info(f"⏳ {label} en progreso…")
            time.sleep(3)


def minutes_running(started_at: str) -> float:
    try:
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - started).total_seconds() / 60
    except Exception:
        return 0


def status_badge(status: str) -> str:
    return {"completed": "✅", "failed": "❌", "running": "⏳", "pending": "🕐"}.get(status, "❓")


# ---------------------------
# Panel de jobs activos
# ---------------------------
st.markdown("---")
st.subheader("Estado de ejecuciones")

jobs = get_recent_jobs(10)
running_jobs = [j for j in jobs if j["status"] == "running"]

if running_jobs:
    for job in running_jobs:
        mins = minutes_running(job["started_at"])
        label = STEP_LABELS.get(job["type"], job["type"])
        col1, col2, col3 = st.columns([3, 2, 2])
        col1.warning(f"⏳ **{label}** en progreso… ({int(mins)} min)")
        col2.caption(f"Job: `{job['job_id'][:8]}…`")
        if mins >= MIN_MINUTES_TO_FORCE_FAIL:
            if col3.button("🛑 Marcar como fallido", key=f"fail_{job['job_id']}"):
                try:
                    r = requests.patch(f"{BACKEND_URL}/jobs/{job['job_id']}/fail", timeout=5)
                    if r.ok:
                        st.success("Job marcado como fallido.")
                        st.rerun()
                    else:
                        st.error(r.json().get("detail", "Error"))
                except Exception as e:
                    st.error(str(e))
        else:
            remaining = int(MIN_MINUTES_TO_FORCE_FAIL - mins)
            col3.caption(f"Botón disponible en {remaining} min")
else:
    st.success("✅ No hay procesos en ejecución.")

st.markdown("---")

# ---------------------------
# Resultado de la última ejecución
# ---------------------------
if "last_job_output" in st.session_state:
    label = st.session_state.get("last_job_label", "")
    ok = st.session_state.get("last_job_ok", True)
    output = st.session_state.get("last_job_output", "")
    error = st.session_state.get("last_job_error", "")

    if ok:
        st.success(f"✅ {label} completado")
    else:
        st.error(f"❌ {label} falló: {error}")

    if output:
        with st.expander("📋 Ver output", expanded=True):
            st.code(output, language=None)

    if st.button("✖ Cerrar resultado"):
        for key in ("last_job_output", "last_job_label", "last_job_ok", "last_job_error"):
            st.session_state.pop(key, None)
        st.rerun()

    st.markdown("---")

# ---------------------------
# Pasos individuales
# ---------------------------
st.subheader("Pasos individuales")

is_busy = len(running_jobs) > 0
if is_busy:
    st.warning("⚠️ Hay un proceso en curso. Los botones están deshabilitados hasta que termine.")

for step_id, label, description in STEPS:
    with st.expander(label):
        st.caption(description)
        if st.button(f"Ejecutar: {label}", key=f"btn_{step_id}", disabled=is_busy):
            try:
                r = requests.post(f"{BACKEND_URL}/pipeline/{step_id}", timeout=10)
                r.raise_for_status()
                job_id = r.json()["job_id"]
                poll_job(job_id, label)
            except requests.RequestException as e:
                st.error(f"Error al lanzar: {e}")

st.markdown("---")

# ---------------------------
# Ejecutar todo
# ---------------------------
st.subheader("Ejecutar pipeline completo")
st.caption("Lanza los 6 pasos en orden. Si uno falla, el proceso se detiene.")

if st.button("🚀 Ejecutar todo el pipeline", type="primary", disabled=is_busy):
    try:
        r = requests.post(f"{BACKEND_URL}/pipeline/run-all", timeout=10)
        r.raise_for_status()
        job_id = r.json()["job_id"]
        poll_job(job_id, "Pipeline completo")
    except requests.RequestException as e:
        st.error(f"Error al lanzar el pipeline: {e}")
