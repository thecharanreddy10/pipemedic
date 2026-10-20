import os
import uuid

import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI(title="orders-service")

INVENTORY_URL = os.getenv("INVENTORY_URL", "http://localhost:8003")
PAYMENTS_URL = os.getenv("PAYMENTS_URL", "http://localhost:8002")
ORDERS: dict[str, dict] = {}


@app.get("/health")
def health():
    return {"service": "orders", "status": "ok"}


@app.post("/orders")
def create_order(item: str, qty: int, amount: float):
    order_id = str(uuid.uuid4())[:8]
    try:
        httpx.post(f"{INVENTORY_URL}/reserve/{item}/{qty}", timeout=5).raise_for_status()
        httpx.post(
            f"{PAYMENTS_URL}/pay", params={"order_id": order_id, "amount": amount}, timeout=5
        ).raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"downstream failure: {e}")
    ORDERS[order_id] = {"id": order_id, "item": item, "qty": qty, "amount": amount, "status": "confirmed"}
    return ORDERS[order_id]


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    if order_id not in ORDERS:
        raise HTTPException(404, "order not found")
    return ORDERS[order_id]
