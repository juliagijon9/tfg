import os
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Prompts", page_icon="📝", layout="wide")
st.title("📝 Gestión de Prompts")
st.caption("Consulta, navega entre versiones y edita los prompts de los LLMs.")

PROMPT_LABELS = {
    "prompt_intention":      "🧠 Extracción de intención",
    "prompt_classification": "🏷️ Clasificación de tickets",
    "prompt_tag":            "🔖 Asignación de tags",
}

PROMPT_ORDER = ["prompt_intention", "prompt_classification", "prompt_tag"]


# ---------------------------
# Cargar lista de prompts
# ---------------------------
try:
    r = requests.get(f"{BACKEND_URL}/prompts", timeout=5)
    prompts = r.json() if r.ok else []
except Exception:
    prompts = []
    st.error("No se puede conectar con el backend.")

if not prompts:
    st.warning("No hay prompts en la base de datos. Ejecuta setup_prompts.py para cargarlos.")
    st.stop()

# ---------------------------
# Layout: izquierda lista / derecha detalle
# ---------------------------
col_list, col_detail = st.columns([1, 3])

with col_list:
    st.subheader("Prompts")
    prompt_names_raw = [p["name"] for p in prompts]
    prompt_names = sorted(prompt_names_raw, key=lambda n: PROMPT_ORDER.index(n) if n in PROMPT_ORDER else 99)
    prompts = sorted(prompts, key=lambda p: PROMPT_ORDER.index(p["name"]) if p["name"] in PROMPT_ORDER else 99)

    if "selected_prompt" not in st.session_state:
        st.session_state.selected_prompt = prompt_names[0]
    if "edit_mode" not in st.session_state:
        st.session_state.edit_mode = False
    if "selected_version" not in st.session_state:
        st.session_state.selected_version = None

    for p in prompts:
        label = PROMPT_LABELS.get(p["name"], p["name"])
        is_selected = p["name"] == st.session_state.selected_prompt
        prefix = "▶" if is_selected else "○"
        if st.button(f"{prefix} {label}", key=f"sel_{p['name']}", use_container_width=True):
            st.session_state.selected_prompt = p["name"]
            st.session_state.selected_version = None
            st.session_state.edit_mode = False
            st.rerun()

with col_detail:
    selected_name = st.session_state.selected_prompt
    label = PROMPT_LABELS.get(selected_name, selected_name)
    st.subheader(label)

    # Cargar versiones disponibles
    try:
        rv = requests.get(f"{BACKEND_URL}/prompts/{selected_name}/versions", timeout=5)
        versions = rv.json() if rv.ok else []
    except Exception:
        versions = []

    if not versions:
        st.warning("No se encontraron versiones para este prompt.")
        st.stop()

    version_numbers = [v["version"] for v in versions]
    latest = version_numbers[0]

    # Selector de versión
    if st.session_state.selected_version is None or st.session_state.selected_version not in version_numbers:
        st.session_state.selected_version = latest

    vcol1, vcol2, vcol3 = st.columns([1, 2, 1])

    with vcol1:
        if st.button("◀", disabled=(st.session_state.selected_version == version_numbers[-1])):
            idx = version_numbers.index(st.session_state.selected_version)
            st.session_state.selected_version = version_numbers[idx + 1]
            st.session_state.edit_mode = False
            st.rerun()

    with vcol2:
        chosen = st.selectbox(
            "Versión",
            version_numbers,
            index=version_numbers.index(st.session_state.selected_version),
            format_func=lambda v: f"v{v}" + (" (activa)" if v == latest else ""),
            label_visibility="collapsed",
        )
        if chosen != st.session_state.selected_version:
            st.session_state.selected_version = chosen
            st.session_state.edit_mode = False
            st.rerun()

    with vcol3:
        if st.button("▶", disabled=(st.session_state.selected_version == version_numbers[0])):
            idx = version_numbers.index(st.session_state.selected_version)
            st.session_state.selected_version = version_numbers[idx - 1]
            st.session_state.edit_mode = False
            st.rerun()

    # Cargar texto de la versión seleccionada
    try:
        rp = requests.get(f"{BACKEND_URL}/prompts/{selected_name}/{st.session_state.selected_version}", timeout=5)
        prompt_data = rp.json() if rp.ok else {}
    except Exception:
        prompt_data = {}

    prompt_text = prompt_data.get("prompt_text", "")
    created_at = prompt_data.get("created_at", "")[:16].replace("T", " ") if prompt_data.get("created_at") else ""

    if created_at:
        st.caption(f"Creado: {created_at}")

    is_active = st.session_state.selected_version == latest
    if not is_active:
        st.info("ℹ️ Estás viendo una versión antigua. La versión activa es la más reciente.")

    st.markdown("---")

    # Modo visualización / edición
    if not st.session_state.edit_mode:
        st.text_area("Texto del prompt", value=prompt_text, height=450, disabled=True, label_visibility="collapsed")
        if st.button("✏️ Editar", disabled=not is_active):
            st.session_state.edit_mode = True
            st.rerun()
        if not is_active:
            st.caption("Solo se puede editar la versión activa.")
    else:
        new_text = st.text_area("Editar prompt", value=prompt_text, height=450, label_visibility="collapsed")
        col_save, col_cancel = st.columns([1, 1])
        with col_save:
            if st.button("💾 Guardar nueva versión", type="primary"):
                if new_text.strip() == prompt_text.strip():
                    st.warning("No hay cambios respecto a la versión actual.")
                else:
                    try:
                        r = requests.post(
                            f"{BACKEND_URL}/prompts/{selected_name}",
                            json={"prompt_text": new_text},
                            timeout=10,
                        )
                        if r.ok:
                            data = r.json()
                            st.success(f"✅ Nueva versión creada: v{data['version']}")
                            st.session_state.selected_version = data["version"]
                            st.session_state.edit_mode = False
                            st.rerun()
                        else:
                            st.error(f"Error: {r.json().get('detail', 'Error desconocido')}")
                    except Exception as e:
                        st.error(str(e))
        with col_cancel:
            if st.button("✖ Cancelar"):
                st.session_state.edit_mode = False
                st.rerun()
