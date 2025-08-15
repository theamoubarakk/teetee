import os
import pandas as pd

# Save directly to your Mac Desktop
# If your username is different, the expanduser("~") handles it automatically.
PAYMENT_XLSX = os.path.expanduser("~/Desktop/payments.xlsx")

def _ensure_dir_for(path: str):
    """Create parent directory if it doesn't exist (Desktop already exists, but this is safe)."""
    folder = os.path.dirname(path)
    if folder and not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)

def save_payment(phone: str, amount: float, method: str, ts: str) -> None:
    """Append one payment row to the Desktop Excel file. Creates it if missing."""
    _ensure_dir_for(PAYMENT_XLSX)

    new_row = {
        "phone": phone,
        "amount": round(amount, 2),
        "method": method,
        "timestamp": ts,
    }

    # Simple, robust approach: read existing (if any), append row, write back
    if os.path.exists(PAYMENT_XLSX):
        try:
            existing = pd.read_excel(PAYMENT_XLSX)
        except Exception:
            # If file is corrupted or unreadable, start fresh
            existing = pd.DataFrame(columns=["phone", "amount", "method", "timestamp"])
        df = pd.concat([existing, pd.DataFrame([new_row])], ignore_index=True)
    else:
        df = pd.DataFrame([new_row])

    df.to_excel(PAYMENT_XLSX, index=False)  # overwrites with the updated data
