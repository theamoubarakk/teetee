import base64
import io
import os
import time
from datetime import datetime, date, timedelta

import requests
import pandas as pd
import streamlit as st

# --- Configuration from Streamlit secrets or env ---
TOKEN      = st.secrets.get("GITHUB_TOKEN",  os.environ.get("GITHUB_TOKEN"))
OWNER      = st.secrets.get("GITHUB_OWNER",  os.environ.get("GITHUB_OWNER", "user"))
REPO       = st.secrets.get("GITHUB_REPO",   os.environ.get("GITHUB_REPO", "your-repo-name"))
BRANCH     = st.secrets.get("GITHUB_BRANCH", os.environ.get("GITHUB_BRANCH", "main"))
PAYMENTS_PATH   = st.secrets.get("GITHUB_FILE_PATH", os.environ.get("GITHUB_FILE_PATH", "payments.xlsx"))
CUSTOMERS_PATH  = st.secrets.get("GITHUB_CUSTOMERS_PATH", os.environ.get("GITHUB_CUSTOMERS_PATH", "customers.xlsx"))

API_BASE = "https://api.github.com"

# ---------- GitHub Helpers ----------
def _headers():
    if not TOKEN:
        raise RuntimeError("Missing GITHUB_TOKEN in Streamlit secrets.")
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}

def _contents_url(path: str) -> str:
    return f"{API_BASE}/repos/{OWNER}/{REPO}/contents/{path}"

def _get_file_info(path: str):
    r = requests.get(_contents_url(path), headers=_headers(), params={"ref": BRANCH})
    if r.status_code == 200:
        data = r.json()
        content_bytes = base64.b64decode(data["content"])
        return data["sha"], content_bytes
    if r.status_code == 404:
        return None, None
    raise RuntimeError(f"GitHub GET {path} failed: {r.status_code} {r.text}")

def _commit_file(path: str, content_bytes: bytes, message: str, sha: str | None):
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(_contents_url(path), headers=_headers(), json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT {path} failed: {r.status_code} {r.text}")

def _excel_bytes_from_df(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

def _df_from_excel_bytes(b: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(b))

# ---------- Customers ----------
def get_customer(phone: str) -> dict | None:
    sha, bytes_ = _get_file_info(CUSTOMERS_PATH)
    if not bytes_:
        return None
    try:
        df = _df_from_excel_bytes(bytes_)
    except Exception:
        return None
    if "phone" not in df.columns:
        return None
    row = df[df["phone"].astype(str) == str(phone)]
    if row.empty:
        return None
    r = row.iloc[0]
    return {
        "phone": str(r.get("phone", "")),
        "birthday": r.get("birthday", None)
    }

def save_or_update_customer(phone: str, birthday_iso: str):
    attempts = 0
    while True:
        attempts += 1
        sha, bytes_ = _get_file_info(CUSTOMERS_PATH)
        if bytes_:
            try:
                df = _df_from_excel_bytes(bytes_)
            except Exception:
                df = pd.DataFrame(columns=["phone", "birthday"])
        else:
            df = pd.DataFrame(columns=["phone", "birthday"])

        phone_str = str(phone)
        mask = df["phone"].astype(str) == phone_str
        if mask.any():
            df.loc[mask, "birthday"] = birthday_iso
        else:
            df = pd.concat([
                df,
                pd.DataFrame([{"phone": phone_str, "birthday": birthday_iso}])
            ], ignore_index=True)

        updated = _excel_bytes_from_df(df)
        try:
            _commit_file(CUSTOMERS_PATH, updated, f"Upsert customer {phone_str}", sha=sha)
            return
        except RuntimeError as e:
            if "409" in str(e) and attempts < 3:
                time.sleep(0.8)
                continue
            raise

# ---------- Payments ----------
def save_payment(phone: str, amount: float, method: str, ts: str) -> None:
    new_row = {
        "phone": str(phone),
        "amount": round(float(amount), 2),
        "method": method,
        "timestamp": ts,
    }

    attempts = 0
    while True:
        attempts += 1
        sha, bytes_ = _get_file_info(PAYMENTS_PATH)
        if bytes_:
            try:
                df = _df_from_excel_bytes(bytes_)
            except Exception:
                df = pd.DataFrame(columns=["phone", "amount", "method", "timestamp"])
        else:
            df = pd.DataFrame(columns=["phone", "amount", "method", "timestamp"])

        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        updated = _excel_bytes_from_df(df)

        try:
            _commit_file(PAYMENTS_PATH, updated, f"Add payment {new_row['phone']} ({method}) {ts}", sha=sha)
            return
        except RuntimeError as e:
            if "409" in str(e) and attempts < 3:
                time.sleep(0.8)
                continue
            raise

# ---------- Loyalty & Birthday Discount ----------
BASE_POINTS_PER_CURRENCY = 1.0
WINDOW_DAYS = 7
DISCOUNT_RATE = 0.15

def _parse_iso(d: str | None) -> date | None:
    if not d or (isinstance(d, float) and pd.isna(d)):
        return None
    try:
        return date.fromisoformat(str(d)[:10])
    except Exception:
        return None

def _in_pre_birthday_window(purchase_dt: date, bday: date) -> bool:
    event_this_year = date(purchase_dt.year, bday.month, bday.day)
    start_window = event_this_year - timedelta(days=WINDOW_DAYS)
    return start_window <= purchase_dt < event_this_year

def apply_birthday_discount(phone: str, amount: float, ts: str) -> tuple[float, float]:
    cust = get_customer(phone)
    bday = _parse_iso(cust.get("birthday") if cust else None)
    discount_applied = 0.0

    try:
        p_dt = datetime.fromisoformat(ts[:19]).date()
    except Exception:
        p_dt = date.today()

    if bday and _in_pre_birthday_window(p_dt, bday):
        discount_applied = amount * DISCOUNT_RATE
        amount -= discount_applied

    return round(amount, 2), round(discount_applied, 2)

def calculate_points_for_amount(amount: float) -> float:
    return float(amount) * BASE_POINTS_PER_CURRENCY

def calculate_total_points(phone: str) -> float:
    sha, bytes_ = _get_file_info(PAYMENTS_PATH)
    if not bytes_:
        return 0.0
    try:
        df = _df_from_excel_bytes(bytes_)
    except Exception:
        return 0.0

    if df.empty:
        return 0.0

    df = df[df["phone"].astype(str) == str(phone)]
    if df.empty:
        return 0.0

    return float(df["amount"].sum() * BASE_POINTS_PER_CURRENCY)

# ---------- Download helper for UI ----------
def get_payments_file_bytes() -> bytes | None:
    """
    Return the current payments.xlsx file bytes from GitHub (or None if not found).
    """
    _, bytes_ = _get_file_info(PAYMENTS_PATH)
    return bytes_
