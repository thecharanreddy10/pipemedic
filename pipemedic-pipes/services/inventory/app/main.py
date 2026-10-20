from fastapi import FastAPI, HTTPException

app = FastAPI(title="inventory-service")

STOCK = {"laptop": 10, "phone": 25, "headphones": 40}


def reserve(item: str, qty: int) -> int:
    """Reserve qty units of item and return the remaining stock."""
    if item not in STOCK:
        raise KeyError(item)
    if qty <= 0 or qty > STOCK[item]:
        raise ValueError("insufficient stock")
    STOCK[item] -= qty
    return STOCK[item]


@app.get("/health")
def health():
    return {"service": "inventory", "status": "ok"}


@app.get("/items")
def items():
    return STOCK


@app.post("/reserve/{item}/{qty}")
def reserve_item(item: str, qty: int):
    try:
        return {"item": item, "remaining": reserve(item, qty)}
    except KeyError:
        raise HTTPException(404, "unknown item")
    except ValueError as e:
        raise HTTPException(409, str(e))
