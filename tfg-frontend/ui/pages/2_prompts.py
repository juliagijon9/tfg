import os
import streamlit as st

st.set_page_config(page_title="Prompts", page_icon="📝", layout="wide")
st.title("📝 Gestión de Prompts")
st.caption("Consulta y actualiza los prompts que usan los LLMs del pipeline.")

st.info("🚧 En construcción — próxima iteración.")

st.markdown("""
**Funcionalidades previstas:**
- Ver el texto actual de cada prompt (última versión)
- Editar y publicar nueva versión
- Consultar el historial de versiones
- Comparar versiones anteriores
""")
