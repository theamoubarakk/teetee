import json, os

PAYMENT_FILE = os.environ.get("PAYMENT_FILE", "payments.json")  # override via env if needed

def _safe_read(path: str):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def load_payments() -> dict:
    return _safe_read(PAYMENT_FILE)

def save_payments(data: dict) -> None:
    with open(PAYMENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def save_payment(phone: str, amount: float, method: str | None = None, ts: str | None = None) -> dict:
    data = load_payments()
    entry = {
        "amount": round(amount, 2),
        "timestamp": ts,
        "method": method
    }
    data.setdefault(phone, []).append(entry)
    save_payments(data)
    return data

