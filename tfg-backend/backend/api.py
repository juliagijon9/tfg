from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="TFG Backend API")


class MessageRequest(BaseModel):
    name: str


@app.get("/")
def root():
    return {"message": "Backend FastAPI funcionando"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/hello")
def hello():
    return {"message": "Hola desde FastAPI"}


@app.post("/greet")
def greet(data: MessageRequest):
    return {"message": f"Hola, {data.name}. Te saluda el backend FastAPI."}