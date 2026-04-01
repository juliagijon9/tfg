import os
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(page_title="TFG Demo", page_icon="🚀", layout="centered")

st.title("Demo TFG: Streamlit + FastAPI + Docker")
st.write("Este frontend en Streamlit consume un backend FastAPI.")

st.subheader("Comprobación de estado del backend")

if st.button("Comprobar backend"):
    try:
        response = requests.get(f"{BACKEND_URL}/health", timeout=5)
        response.raise_for_status()
        st.success("Backend disponible")
        st.json(response.json())
    except requests.RequestException as e:
        st.error(f"No se pudo conectar con el backend: {e}")

st.subheader("Mensaje simple del backend")

if st.button("Pedir saludo"):
    try:
        response = requests.get(f"{BACKEND_URL}/hello", timeout=5)
        response.raise_for_status()
        data = response.json()
        st.success(data["message"])
    except requests.RequestException as e:
        st.error(f"Error al pedir el saludo: {e}")

st.subheader("Enviar nombre al backend")

name = st.text_input("Escribe tu nombre")

if st.button("Enviar al backend"):
    if not name.strip():
        st.warning("Introduce un nombre")
    else:
        try:
            response = requests.post(
                f"{BACKEND_URL}/greet",
                json={"name": name},
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()
            st.success(data["message"])
        except requests.RequestException as e:
            st.error(f"Error al enviar datos al backend: {e}")

st.markdown("---")
st.caption(f"Backend URL actual: {BACKEND_URL}")