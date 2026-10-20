import uuid

from fastapi import FastAPI, HTTPException

app = FastAPI(title="payments-service")


@app.get("/health")
def health():
    return {"service": "payments", "status": "ok"}


@app.post("/pay")
def pay(order_id: str, amount: float):
    if amount <= 0:
        raise HTTPException(400, "amount must be positive")
    return {
        "order_id": order_id,
        "amount": amount,
        "transaction_id": str(uuid.uuid4()),
        "status": "approved",
    }
