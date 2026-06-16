import os
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

# ---------------------------
# Bloque 0 — Cabecera
# ---------------------------
st.set_page_config(page_title="Estadísticas", page_icon="📊", layout="wide")
st.title("📊 Estadísticas del Pipeline")
st.caption("Análisis de los tickets procesados por el sistema de triaje")
st.divider()

# ---------------------------
# Bloque 1 — Selector de fechas
# ---------------------------
st.subheader("🗓️ Período de análisis")

col1, col2, col3 = st.columns([2, 2, 1])
with col1:
    fecha_inicio = st.date_input("Fecha inicio", value=date.today() - timedelta(days=30), key="fecha_inicio")
with col2:
    fecha_fin = st.date_input("Fecha fin", value=date.today(), key="fecha_fin")
with col3:
    st.write("")
    buscar = st.button("🔍 Consultar", use_container_width=True, type="primary")

if fecha_inicio > fecha_fin:
    st.error("❌ La fecha de inicio no puede ser posterior a la fecha fin.")
    st.stop()

st.divider()

# ---------------------------
# Bloque 2 — Llamada al backend
# ---------------------------
if buscar:
    with st.spinner("⏳ Cargando estadísticas..."):
        try:
            response = requests.get(
                f"{BACKEND_URL}/stats/detalle",
                params={"fecha_inicio": str(fecha_inicio), "fecha_fin": str(fecha_fin)},
                timeout=30,
            )
            if not response.ok:
                st.error("❌ Error al conectar con el backend.")
                st.stop()
            st.session_state["stats_data"] = response.json()
        except Exception as e:
            st.error(f"❌ Error al conectar con el backend: {e}")
            st.stop()

if "stats_data" not in st.session_state:
    st.info("Selecciona un período y pulsa **🔍 Consultar** para cargar las estadísticas.")
    st.stop()

data = st.session_state["stats_data"]

# ---------------------------
# Bloque 3 — Métricas resumen
# ---------------------------
total_tickets = sum(r["n_item"] for r in data["tickets_por_tipo"])
total_embeddings = data["total_embeddings"]
total_relaciones = sum(r["n_item"] for r in data["relaciones"])

total_intenciones = sum(r["n_item"] for r in data["intenciones_confianza"])
total_clasificados = sum(r["n_item"] for r in data["clasificaciones_confianza"])
total_tags = sum(r["n_item"] for r in data["tags_confianza"])

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Tickets", f"{total_tickets:,}")
col2.metric("Embeddings", f"{total_embeddings:,}")
col3.metric("Relaciones", f"{total_relaciones:,}")
col4.metric("Intenciones", f"{total_intenciones:,}")
col5.metric("Clasificados", f"{total_clasificados:,}")
col6.metric("Tags", f"{total_tags:,}")

st.divider()

# ---------------------------
# Colores por nivel de confianza
# ---------------------------
COLORES_CONFIANZA = {4: "#d4edda", 3: "#fff3cd", 2: "#ffd8b1", 1: "#f8d7da"}

def color_fila_confianza(row):
    nivel = row.get("Nivel", None)
    color = COLORES_CONFIANZA.get(nivel, "")
    bg = f"background-color: {color}" if color else ""
    return [bg] * len(row)

# ---------------------------
# Bloque 4 — Tabla 1: Tickets por tipo
# ---------------------------
st.subheader("📋 Tabla 1: Tickets por tipo de Work Item")

if not data["tickets_por_tipo"]:
    st.warning("⚠️ No hay datos de tickets para el período seleccionado.")
else:
    rows = []
    for r in data["tickets_por_tipo"]:
        pct = round(r["n_item"] / total_tickets * 100, 1) if total_tickets else 0
        rows.append({"Tipo de Work Item": r["work_item_type"], "Cantidad": r["n_item"], "Porcentaje (%)": pct})
    rows.append({"Tipo de Work Item": "TOTAL", "Cantidad": total_tickets, "Porcentaje (%)": 100.0})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.divider()

# ---------------------------
# Bloque 5 — Tabla 2: Relaciones por similitud
# ---------------------------
st.subheader("🔗 Tabla 2: Detección de tickets duplicados y relacionados")
st.caption("Umbrales: ≥ 0.92 Duplicado probable · ≥ 0.82 Muy relacionado · ≥ 0.80 Relacionado")

if not data["relaciones"]:
    st.warning("⚠️ No hay datos de relaciones para el período seleccionado.")
else:
    rows = []
    for r in data["relaciones"]:
        pct = round(r["n_item"] / total_relaciones * 100, 1) if total_relaciones else 0
        rows.append({"Nivel de Similitud": r["nivel"], "Cantidad": r["n_item"], "Porcentaje (%)": pct})
    rows.append({"Nivel de Similitud": "TOTAL", "Cantidad": total_relaciones, "Porcentaje (%)": 100.0})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.divider()

# ---------------------------
# Bloque 6 — Tabla 3: Nivel de confianza de la intención
# ---------------------------
st.subheader("🎯 Tabla 3: Confianza en la extracción de intención")
st.caption(
    "Indica con qué fiabilidad el LLM pudo extraer la intención real del ticket, "
    "en función de la información disponible en él. "
    "4 = Excelente · 3 = Suficiente · 2 = Insuficiente · 1 = Muy deficiente"
)

if not data["intenciones_confianza"]:
    st.warning("⚠️ No hay datos de intenciones para el período seleccionado.")
else:
    total_int = sum(r["n_item"] for r in data["intenciones_confianza"])
    rows = []
    for r in data["intenciones_confianza"]:
        pct = round(r["n_item"] / total_int * 100, 1) if total_int else 0
        rows.append({
            "Nivel": r["nivel_confianza_intencion_id"],
            "Confianza en la intención": r["nivel_confianza_intencion_dec"] or "—",
            "Modelo IA": r.get("model") or "—",
            "Cantidad": r["n_item"],
            "Porcentaje (%)": round(pct, 2),
        })
    df = pd.DataFrame(rows)
    total_row = pd.DataFrame([{"Nivel": None, "Confianza en la intención": "TOTAL", "Cantidad": total_int, "Porcentaje (%)": 100.0}])
    df = pd.concat([df, total_row], ignore_index=True)
    styled = df.style.apply(color_fila_confianza, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------
# Bloque 7 — Tabla 4: Clasificación por área y confianza
# ---------------------------
st.subheader("🏷️ Tabla 4: Clasificación por área y nivel de confianza")
st.caption("Distribución de tickets por área funcional asignada, desglosada por confianza en la intención extraída.")

if not data["clasificaciones_confianza"]:
    st.warning("⚠️ No hay datos de clasificaciones para el período seleccionado.")
else:
    rows = [
        {
            "Área": r["area"],
            "Nivel": r["nivel_confianza_intencion_id"],
            "Confianza en la intención": r["nivel_confianza_intencion_dec"] or "—",
            "Modelo IA": r.get("model") or "—",
            "Cantidad": r["n_item"],
        }
        for r in data["clasificaciones_confianza"]
    ]
    df = pd.DataFrame(rows)
    styled = df.style.apply(color_fila_confianza, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------
# Bloque 8 — Tabla 5: Tags por nivel de confianza
# ---------------------------
st.subheader("🔖 Tabla 5: Tags asignados por nivel de confianza")
st.caption("Tags descriptivos asignados a los tickets, desglosados por confianza en la intención extraída.")

if not data["tags_confianza"]:
    st.warning("⚠️ No hay datos de tags para el período seleccionado.")
else:
    rows = [
        {
            "Tag": r["tag"],
            "Nivel": r["nivel_confianza_intencion_id"],
            "Confianza en la intención": r["nivel_confianza_intencion_dec"] or "—",
            "Modelo IA": r.get("model") or "—",
            "Cantidad": r["n_item"],
        }
        for r in data["tags_confianza"]
    ]
    df = pd.DataFrame(rows)
    styled = df.style.apply(color_fila_confianza, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)

# ---------------------------
# Bloque 9 — Pie de página
# ---------------------------
st.divider()
st.info(
    f"📅 Período consultado: {fecha_inicio.strftime('%d/%m/%Y')} → "
    f"{fecha_fin.strftime('%d/%m/%Y')}  |  "
    f"🕐 Última actualización: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
)
