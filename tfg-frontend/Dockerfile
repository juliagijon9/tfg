FROM python:3.11-slim

WORKDIR /ui

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY ui .

EXPOSE 8501

CMD ["streamlit", "run", "/ui/app.py", "--server.address=0.0.0.0", "--server.port=8501"]