import os

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="frontend-service")

INVENTORY_URL = os.getenv("INVENTORY_URL", "http://localhost:8003")


@app.get("/health")
def health():
    return {"service": "frontend", "status": "ok"}


@app.get("/api/catalog")
def catalog():
    try:
        return httpx.get(f"{INVENTORY_URL}/items", timeout=3).json()
    except httpx.HTTPError:
        return {"error": "inventory unavailable"}


@app.get("/", response_class=HTMLResponse)
def home():
    return "<h1>PipeMedic Shop</h1><p>Browse the catalog at <a href='/api/catalog'>/api/catalog</a>.</p>"