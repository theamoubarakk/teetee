import os, json
import pandas as pd

# ---- file locations (override via env if you like) ----
PAYMENT_JSON   = os.environ.get("PAYMENT_JSON",  "data/payments.json")
PAYMENT_XLSX   = os.environ.get("PAYMENTS_XLSX", "data/payments.xlsx")

def _ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

def _safe_read_json(path: str):
    if os.path.exists(path):
        try:
            return json.load(open(path, "r", encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}

def _write_json(path: str, data: dict):
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _append_excel(path: str, row: dict):
    _ensure_dir(path)
    df_new = pd.DataFrame([row])
    if os.path.exists(path):
        # append to existing file
        with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="overlay") as writer:
            try:
                # read existing to get last row index; then write below
                existing = pd.read_excel(path)
                startrow = len(existing) + 1
            except Exception:
                startrow = 0
            df_new.to_excel(writer, index=False, header=not os.path.exists(path) or startrow == 0, startrow=startrow)
    else:
        # create new file
        with pd.ExcelWriter(path, engine="openpyxl", mode="w") as writer:
            df_new.to_excel(writer, index=False)

def save_payment(phone: str, amount: float, method: str, ts: str) -> None:
    # 1) JSON history (optional but handy)
    data = _safe_read_json(PAYMENT_JSON)
    entry = {"phone": phone, "amount": round(amount, 2), "method": method, "timestamp": ts}
    data.setdefault(phone, []).append({k: v for k, v in entry.items() if k != "phone"})
    _write_json(PAYMENT_JSON, data)

    # 2) Excel log (authoritative record you keep on your PC / repo)
    _append_excel(PAYMENT_XLSX, entry)
