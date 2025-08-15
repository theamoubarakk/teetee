import os
import pandas as pd

# Save in the same folder as storage.py
PAYMENT_XLSX = os.path.join(os.path.dirname(__file__), "payments.xlsx")

def save_payment(phone: str, amount: float, method: str, ts: str) -> None:
    """Append one payment row to payments.xlsx in the repo folder."""
    new_row = {
        "phone": phone,
        "amount": round(amount, 2),
        "method": method,
        "timestamp": ts,
    }

    if os.path.exists(PAYMENT_XLSX):
        try:
            existing = pd.read_excel(PAYMENT_XLSX)
        except Exception:
            existing = pd.DataFrame(columns=["phone", "amount", "method", "timestamp"])
        df = pd.concat([existing, pd.DataFrame([new_row])], ignore_index=True)
    else:
        df = pd.DataFrame([new_row])

    df.to_excel(PAYMENT_XLSX, index=False)

def get_excel_file():
    """Return the Excel file bytes if it exists."""
    if os.path.exists(PAYMENT_XLSX):
        with open(PAYMENT_XLSX, "rb") as f:
            return f.read()
    return None
