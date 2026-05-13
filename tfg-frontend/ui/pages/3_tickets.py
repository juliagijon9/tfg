import html as html_module
import os
import re

import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="Tickets", page_icon="🎫", layout="wide")
st.title("🎫 Consulta de Tickets")


def clean_html(text: str | None) -> str:
    """Elimina etiquetas HTML y devuelve texto plano."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------
# Búsqueda por ID
# ---------------------------
col_input, col_btn = st.columns([2, 1])
with col_input:
    ticket_id_str = st.text_input(
        "ID", label_visibility="collapsed", placeholder="ID del ticket…"
    )
with col_btn:
    buscar = st.button("🔍 Buscar", type="primary", use_container_width=True)

if buscar:
    if not ticket_id_str.strip().isdigit():
        st.error("Introduce un ID numérico válido.")
        st.stop()
    ticket_id = int(ticket_id_str.strip())
    with st.spinner("Buscando…"):
        try:
            r = requests.get(f"{BACKEND_URL}/tickets/{ticket_id}", timeout=10)
            if r.status_code == 404:
                st.error(f"Ticket {ticket_id} no encontrado.")
                st.session_state.pop("ticket_data", None)
                st.stop()
            st.session_state.ticket_data = r.json()
            st.session_state.ticket_id = ticket_id
            st.session_state.expanded_dup = None
        except Exception as e:
            st.error(str(e))
            st.stop()

ticket = st.session_state.get("ticket_data")
if not ticket:
    st.stop()


# ---------------------------
# Helper: campos compactos de un ticket
# ---------------------------
def render_ticket_fields(t: dict, use_expanders: bool = True):
    """Muestra campos del ticket. use_expanders=False para evitar nesting."""
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Tipo:** {t.get('work_item_type') or '—'}")
        st.markdown(f"**Estado:** {t.get('state') or '—'}")
        st.markdown(f"**Área:** {t.get('area_path') or '—'}")
    with col2:
        st.markdown(f"**Asignado:** {t.get('assigned_to') or '—'}")
        st.markdown(f"**Iteración:** {t.get('iteration_path') or '—'}")
        st.markdown(f"**Creado:** {(t.get('created_date') or '')[:10]} &nbsp; **Modificado:** {(t.get('changed_date') or '')[:10]}")
    if t.get("tags"):
        st.markdown(f"**Tags ADO:** `{t['tags']}`")

    desc = clean_html(t.get("description"))
    repro = clean_html(t.get("repro_steps"))
    ac = clean_html(t.get("acceptance_criteria"))

    if use_expanders:
        if desc:
            with st.expander("📄 Descripción"):
                st.text(desc)
        if repro:
            with st.expander("🔁 Pasos para reproducir"):
                st.text(repro)
        if ac:
            with st.expander("✅ Criterios de aceptación"):
                st.text(ac)
    else:
        if desc:
            st.markdown("**📄 Descripción**")
            st.text(desc)
        if repro:
            st.markdown("**🔁 Pasos para reproducir**")
            st.text(repro)
        if ac:
            st.markdown("**✅ Criterios de aceptación**")
            st.text(ac)


# ---------------------------
# Sección 1 — Datos del ticket
# ---------------------------
st.markdown("---")
st.subheader(f"#{ticket['id']} — {ticket['title']}")
render_ticket_fields(ticket)


# ---------------------------
# Sección 2 — Triaje
# ---------------------------
st.markdown("---")

try:
    rt = requests.get(f"{BACKEND_URL}/tickets/{ticket['id']}/triage", timeout=10)
    triage = rt.json() if rt.ok else {}
except Exception:
    triage = {}

with st.expander("🧠 Intención, Clasificación y Tags", expanded=False):
    if not triage:
        st.info("Este ticket aún no tiene triaje. Ejecuta el pipeline para procesarlo.")
    else:
        st.markdown("**Intención**")
        st.info(triage.get("intention") or "—")
        st.caption(f"Extraído: {(triage.get('extracted_at') or '')[:16].replace('T', ' ')}")

        st.markdown("---")
        st.markdown("**Clasificación**")
        col1, col2 = st.columns([1, 3])
        col1.markdown(f"**Área:** {triage.get('area') or '—'}")
        col2.markdown(f"**Justificación:** {triage.get('justification') or '—'}")
        st.caption(
            f"Clasificado: {(triage.get('classified_at') or '')[:16].replace('T', ' ')} "
            f"· Modelo: {triage.get('model') or '—'}"
        )

        st.markdown("---")
        st.markdown("**Tags**")
        tags = triage.get("tags") or []
        if tags:
            st.markdown(" · ".join(f"`{t}`" for t in tags))
        else:
            st.markdown("—")
        st.caption(f"Taggeado: {(triage.get('extracted_tag_at') or '')[:16].replace('T', ' ')}")


# ---------------------------
# Sección 3 — Duplicados y relacionados
# ---------------------------
st.markdown("---")

try:
    rd = requests.get(f"{BACKEND_URL}/tickets/{ticket['id']}/duplicates", timeout=10)
    duplicates = rd.json() if rd.ok else []
except Exception:
    duplicates = []

st.markdown(f"**🔗 Duplicados y relacionados ({len(duplicates)})**")

if not duplicates:
    st.info("No se encontraron tickets duplicados o relacionados.")
else:
    for i, d in enumerate(duplicates):
        rel = d.get("relation_type") or "—"
        sim = f"{d['similarity']:.1%}" if d.get("similarity") else "—"
        dup_id = d.get("id") or d.get("id_dup")
        dup_title = d.get("title") or d.get("title_dup") or "—"
        badge = "❗" if rel == "duplicate" else "🔁"

        with st.expander(f"{badge} #{dup_id} · {rel} · {sim} · {dup_title[:70]}"):
            dup_ticket = {
                "work_item_type":      d.get("work_item_type") or d.get("work_item_type_dup"),
                "title":               d.get("title") or d.get("title_dup"),
                "state":               d.get("state") or d.get("state_dup"),
                "created_date":        d.get("created_date") or d.get("created_date_dup"),
                "changed_date":        d.get("changed_date") or d.get("changed_date_dup"),
                "area_path":           d.get("area_path") or d.get("area_path_dup"),
                "iteration_path":      d.get("iteration_path") or d.get("iteration_path_dup"),
                "assigned_to":         d.get("assigned_to") or d.get("assigned_to_dup"),
                "tags":                d.get("tags") or d.get("tags_dup"),
                "description":         d.get("description") or d.get("description_dup"),
                "repro_steps":         d.get("repro_steps") or d.get("repro_steps_dup"),
                "acceptance_criteria": d.get("acceptance_criteria") or d.get("acceptance_criteria_dup"),
            }
            render_ticket_fields(dup_ticket, use_expanders=False)
