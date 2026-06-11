import os
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Modelos de IA", page_icon="🤖", layout="wide")
st.title("🤖 Modelos de IA")
st.caption("Deployments de Azure OpenAI disponibles. Cada prompt puede usar un modelo distinto.")


def load_modelos():
    try:
        r = requests.get(f"{BACKEND_URL}/modelos-ia", timeout=5)
        return r.json() if r.ok else []
    except Exception:
        return []


# ---------------------------
# Sección 1 — Listado de modelos
# ---------------------------
st.markdown("---")
st.subheader("Modelos disponibles")

modelos = load_modelos()

if not modelos:
    st.info("No hay modelos registrados todavía.")
else:
    for m in modelos:
        col_info, col_edit, col_toggle, col_del = st.columns([5, 1, 1, 1])

        with col_info:
            badge = "🟢 Activo" if m["active"] else "🔴 Inactivo"
            st.markdown(f"**{m['name']}** · `{m['deployment']}` · {badge}")
            if m.get("description"):
                st.caption(m["description"])

        with col_edit:
            if st.button("✏️", key=f"edit_{m['id']}", help="Editar modelo"):
                st.session_state[f"editing_{m['id']}"] = True

        with col_toggle:
            label = "🔴" if m["active"] else "🟢"
            help_txt = "Desactivar" if m["active"] else "Activar"
            if st.button(label, key=f"toggle_{m['id']}", help=help_txt):
                r = requests.put(f"{BACKEND_URL}/modelos-ia/{m['id']}", json={"active": not m["active"]}, timeout=5)
                if r.ok:
                    st.rerun()
                else:
                    st.error(r.json().get("detail", "Error"))

        with col_del:
            if st.button("🗑️", key=f"del_{m['id']}", help="Eliminar modelo"):
                st.session_state[f"confirm_del_{m['id']}"] = True

        # Formulario de edición inline
        if st.session_state.get(f"editing_{m['id']}"):
            with st.container(border=True):
                st.markdown(f"**Editando: {m['name']}**")
                ed_name = st.text_input("Nombre", value=m["name"], key=f"ed_name_{m['id']}")
                ed_desc = st.text_input("Descripción", value=m.get("description") or "", key=f"ed_desc_{m['id']}")
                c1, c2 = st.columns([1, 1])
                with c1:
                    if st.button("💾 Guardar", key=f"ed_save_{m['id']}", type="primary"):
                        r = requests.put(
                            f"{BACKEND_URL}/modelos-ia/{m['id']}",
                            json={"name": ed_name.strip(), "description": ed_desc.strip() or None},
                            timeout=5,
                        )
                        if r.ok:
                            st.success("Modelo actualizado.")
                            st.session_state.pop(f"editing_{m['id']}", None)
                            st.rerun()
                        else:
                            st.error(r.json().get("detail", "Error"))
                with c2:
                    if st.button("✖ Cancelar", key=f"ed_cancel_{m['id']}"):
                        st.session_state.pop(f"editing_{m['id']}", None)
                        st.rerun()

        # Confirmación de borrado
        if st.session_state.get(f"confirm_del_{m['id']}"):
            st.warning(f"¿Eliminar el modelo **{m['name']}** (`{m['deployment']}`)? No se puede eliminar si hay prompts que lo usan.")
            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("✅ Sí, eliminar", key=f"del_yes_{m['id']}", type="primary"):
                    r = requests.delete(f"{BACKEND_URL}/modelos-ia/{m['id']}", timeout=5)
                    if r.ok:
                        st.success(f"Modelo '{m['name']}' eliminado.")
                        st.session_state.pop(f"confirm_del_{m['id']}", None)
                        st.rerun()
                    else:
                        st.error(r.json().get("detail", "Error al eliminar"))
            with c2:
                if st.button("❌ Cancelar", key=f"del_no_{m['id']}"):
                    st.session_state.pop(f"confirm_del_{m['id']}", None)
                    st.rerun()

        st.divider()


# ---------------------------
# Sección 2 — Añadir nuevo modelo
# ---------------------------
st.markdown("---")
st.subheader("Añadir nuevo modelo")

with st.form("form_nuevo_modelo"):
    col_n, col_d = st.columns([1, 1])
    with col_n:
        nuevo_name = st.text_input("Nombre legible", placeholder="ej. GPT-5.4")
    with col_d:
        nuevo_deployment = st.text_input("Nombre del deployment en Azure", placeholder="ej. gpt-5.4")
    nuevo_desc = st.text_input("Descripción (opcional)", placeholder="Uso recomendado de este modelo")
    submitted = st.form_submit_button("➕ Añadir modelo", type="primary")
    if submitted:
        if not nuevo_name.strip() or not nuevo_deployment.strip():
            st.error("El nombre y el deployment son obligatorios.")
        else:
            r = requests.post(
                f"{BACKEND_URL}/modelos-ia",
                json={"name": nuevo_name.strip(), "deployment": nuevo_deployment.strip(), "description": nuevo_desc.strip() or None},
                timeout=5,
            )
            if r.ok:
                st.success(f"✅ Modelo '{nuevo_name}' añadido correctamente.")
                st.rerun()
            elif r.status_code == 409:
                st.warning(r.json().get("detail", "El deployment ya existe"))
            else:
                st.error(r.json().get("detail", "Error al añadir"))
