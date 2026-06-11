import html as html_module
import os
import re
from collections import defaultdict

import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Modelos de Clasificación", page_icon="🏷️", layout="wide")
st.title("🏷️ Modelos de Clasificación")
st.caption("Tickets de referencia usados por la IA para clasificar correctamente los tickets por área.")


def clean_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def load_modelos():
    try:
        r = requests.get(f"{BACKEND_URL}/modelos-clasificacion", timeout=10)
        return r.json() if r.ok else []
    except Exception:
        return []


# ---------------------------
# Sección 1 — Listado agrupado por área
# ---------------------------
st.markdown("---")
st.subheader("Tickets modelo actuales")

modelos = load_modelos()

if not modelos:
    st.info("No hay tickets modelo cargados todavía.")
else:
    # Agrupa por área correcta
    por_area = defaultdict(list)
    for m in modelos:
        area = m.get("area") or "Sin área"
        por_area[area].append(m)

    for area, tickets in sorted(por_area.items()):
        st.markdown(f"**{area}** ({len(tickets)} ticket{'s' if len(tickets) != 1 else ''})")
        for idx, m in enumerate(tickets, 1):
            col_info, col_btn = st.columns([10, 1])
            with col_info:
                with st.expander(f"{idx}. #{m['id']} · {m['work_item_type'] or '—'} · {(m['title'] or '')[:80]}"):
                    c1, c2 = st.columns(2)
                    c1.markdown(f"**Área ADO:** {m.get('area_path') or '—'}")
                    c1.markdown(f"**Área correcta:** `{m.get('area') or '—'}`")
                    c2.markdown(f"**Iteración:** {m.get('iteration_path') or '—'}")
                    c2.markdown(f"**Tags:** {m.get('tags') or '—'}")
                    desc = clean_html(m.get("description"))
                    repro = clean_html(m.get("repro_steps"))
                    ac = clean_html(m.get("acceptance_criteria"))
                    if desc:
                        st.markdown(f"**Descripción:** {desc[:300]}{'…' if len(desc) > 300 else ''}")
                    if repro:
                        st.markdown(f"**Pasos:** {repro[:300]}{'…' if len(repro) > 300 else ''}")
                    if ac:
                        st.markdown(f"**Criterios:** {ac[:300]}{'…' if len(ac) > 300 else ''}")

            with col_btn:
                if st.button("🗑️", key=f"del_btn_{m['id']}", help="Eliminar este modelo"):
                    st.session_state[f"confirm_delete_{m['id']}"] = True

            if st.session_state.get(f"confirm_delete_{m['id']}"):
                st.warning(f"¿Seguro que quieres eliminar el ticket **#{m['id']} — {m['title']}** de los modelos?")
                c_yes, c_no = st.columns([1, 1])
                with c_yes:
                    if st.button("✅ Sí, eliminar", key=f"confirm_yes_{m['id']}", type="primary"):
                        r = requests.delete(f"{BACKEND_URL}/modelos-clasificacion/{m['id']}", timeout=10)
                        if r.ok:
                            st.success(f"Ticket #{m['id']} eliminado de los modelos.")
                            st.session_state.pop(f"confirm_delete_{m['id']}", None)
                            st.rerun()
                        else:
                            st.error(r.json().get("detail", "Error al eliminar"))
                with c_no:
                    if st.button("❌ Cancelar", key=f"confirm_no_{m['id']}"):
                        st.session_state.pop(f"confirm_delete_{m['id']}", None)
                        st.rerun()

        st.markdown("")  # Separador visual entre áreas


# ---------------------------
# Sección 2 — Añadir nuevo ticket modelo
# ---------------------------
st.markdown("---")
st.subheader("Añadir nuevo ticket modelo")

col_input, col_btn = st.columns([2, 1])
with col_input:
    ticket_id_str = st.text_input("ID del ticket", label_visibility="collapsed", placeholder="ID del ticket a añadir como modelo…")
with col_btn:
    buscar = st.button("🔍 Previsualizar", type="primary", use_container_width=True)

if buscar:
    if not ticket_id_str.strip().isdigit():
        st.error("Introduce un ID numérico válido.")
        st.stop()
    tid = int(ticket_id_str.strip())

    existe = any(m["id"] == tid for m in modelos)
    if existe:
        st.warning(f"El ticket #{tid} ya existe como modelo de clasificación.")
        st.stop()

    with st.spinner("Buscando ticket…"):
        try:
            r = requests.get(f"{BACKEND_URL}/modelos-clasificacion/preview/{tid}", timeout=10)
            if r.status_code == 404:
                st.error(f"Ticket {tid} no encontrado o no tiene clasificación asignada.")
                st.stop()
            st.session_state["preview_modelo"] = r.json()
        except Exception as e:
            st.error(str(e))
            st.stop()

preview = st.session_state.get("preview_modelo")
if preview:
    st.markdown("**Vista previa del ticket a añadir:**")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        c1.markdown(f"**ID:** {preview['id']}")
        c1.markdown(f"**Tipo:** {preview.get('work_item_type') or '—'}")
        c1.markdown(f"**Área ADO:** {preview.get('area_path') or '—'}")
        c1.markdown(f"**Área correcta:** `{preview.get('area') or '—'}`")
        c2.markdown(f"**Título:** {preview.get('title') or '—'}")
        c2.markdown(f"**Iteración:** {preview.get('iteration_path') or '—'}")
        c2.markdown(f"**Tags:** {preview.get('tags') or '—'}")
        desc = clean_html(preview.get("description"))
        if desc:
            st.markdown(f"**Descripción:** {desc[:400]}{'…' if len(desc) > 400 else ''}")

    if st.button("✅ Confirmar y añadir como modelo", type="primary"):
        r = requests.post(f"{BACKEND_URL}/modelos-clasificacion/{preview['id']}", timeout=10)
        if r.ok:
            st.success(f"✅ Ticket #{preview['id']} añadido como modelo de clasificación.")
            st.session_state.pop("preview_modelo", None)
            st.rerun()
        elif r.status_code == 409:
            st.warning(r.json().get("detail", "Ya existe"))
        else:
            st.error(r.json().get("detail", "Error al añadir"))
