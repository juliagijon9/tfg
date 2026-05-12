import os
import streamlit as st

st.set_page_config(page_title="Tickets", page_icon="🎫", layout="wide")
st.title("🎫 Consulta de Tickets")
st.caption("Busca, filtra y gestiona tickets y su triaje.")

st.info("🚧 En construcción — próxima iteración.")

st.markdown("""
**Funcionalidades previstas:**
- Listado de tickets con filtros (área, tipo, fecha, tag)
- Detalle de un ticket con su intención, clasificación y tags
- Borrar el triaje de un ticket individual
- Forzar retriaje de un ticket concreto
""")
