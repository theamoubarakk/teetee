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

def _headers():
    if not TOKEN:
        raise RuntimeError("Missing GITHUB_TOKEN in Streamlit secrets.")
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}

def _contents_url(path: str) -> str:
    return f"{API_BASE}/repos/{OWNER}/{REPO}/contents/{path}"

def _get_file_info(path: str):
    """Return (sha, bytes) of the file at path on BRANCH, or (None, None) if not found."""
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

# ---------- Customers (birthday/anniversary) ----------
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
        "birthday": r.get("birthday", None),
        "anniversary": r.get("anniversary", None),
    }

def save_or_update_customer(phone: str, birthday_iso: str, anniversary_iso: str | None):
    """Create or update a customer (phone, birthday, anniversary) and commit to GitHub."""
    attempts = 0
    while True:
        attempts += 1
        sha, bytes_ = _get_file_info(CUSTOMERS_PATH)
        if bytes_:
            try:
                df = _df_from_excel_bytes(bytes_)
            except Exception:
                df = pd.DataFrame(columns=["phone", "birthday", "anniversary"])
        else:
            df = pd.DataFrame(columns=["phone", "birthday", "anniversary"])

        # Upsert by phone
        phone_str = str(phone)
        if "phone" not in df.columns:
            df = pd.DataFrame(columns=["phone", "birthday", "anniversary"])
        mask = df["phone"].astype(str) == phone_str
        if mask.any():
            df.loc[mask, "birthday"] = birthday_iso
            df.loc[mask, "anniversary"] = anniversary_iso
        else:
            df = pd.concat([
                df,
                pd.DataFrame([{
                    "phone": phone_str,
                    "birthday": birthday_iso,
                    "anniversary": anniversary_iso
                }])
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
    """Append one payment row to payments.xlsx and commit to GitHub with retry."""
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

# ---------- Loyalty points ----------
BASE_POINTS_PER_CURRENCY = 1.0  # 1 point per $1 (or per currency unit)
WINDOW_DAYS = 7                 # 7 days before birthday/anniversary
MULTIPLIER = 1.5                # 1.5× within the window

def _parse_iso(d: str | None) -> date | None:
    if not d or (isinstance(d, float) and pd.isna(d)):
        return None
    try:
        return date.fromisoformat(str(d)[:10])
    except Exception:
        return None

def _in_pre_event_window(purchase_dt: date, event_md: tuple[int, int]) -> bool:
    """
    True if purchase_dt is within WINDOW_DAYS BEFORE the event date in that year.
    Handles year wrap (e.g., event Jan 3 and purchase on Dec 28).
    """
    event_day_this_year = date(purchase_dt.year, event_md[0], event_md[1])
    start_window = event_day_this_year - timedelta(days=WINDOW_DAYS)
    if start_window <= purchase_dt < event_day_this_year:
        return True

    # Also check previous/next year for wrap-around scenarios
    event_prev_year = date(purchase_dt.year - 1, event_md[0], event_md[1])
    start_prev = event_prev_year - timedelta(days=WINDOW_DAYS)
    if start_prev <= purchase_dt < event_prev_year:
        return True

    event_next_year = date(purchase_dt.year + 1, event_md[0], event_md[1])
    start_next = event_next_year - timedelta(days=WINDOW_DAYS)
    if start_next <= purchase_dt < event_next_year:
        return True

    return False

def _multiplier_for_purchase(ts_iso: str, birthday_iso: str | None, anniversary_iso: str | None) -> float:
    try:
        p_dt = datetime.fromisoformat(ts_iso[:19]).date()
    except Exception:
        p_dt = date.today()

    apply_bonus = False
    bday = _parse_iso(birthday_iso)
    if bday:
        apply_bonus = apply_bonus or _in_pre_event_window(p_dt, (bday.month, bday.day))
    ann = _parse_iso(anniversary_iso)
    if ann:
        apply_bonus = apply_bonus or _in_pre_event_window(p_dt, (ann.month, ann.day))

    return MULTIPLIER if apply_bonus else 1.0

def calculate_points_for_amount(phone: str, amount: float, ts: str) -> tuple[float, float]:
    """
    Points for a single purchase amount at time ts, based on the customer's profile.
    Returns (earned_points, multiplier).
    """
    cust = get_customer(phone)
    birthday_iso = cust.get("birthday") if cust else None
    anniversary_iso = cust.get("anniversary") if cust else None

    mult = _multiplier_for_purchase(ts, birthday_iso, anniversary_iso)
    points = amount * BASE_POINTS_PER_CURRENCY * mult
    return float(points), float(mult)

def calculate_total_points(phone: str) -> float:
    """
    Sum points across all payments for this phone, applying the correct multiplier
    for each payment based on the customer's birthday/anniversary.
    """
    # Load customer dates
    cust = get_customer(phone)
    birthday_iso = cust.get("birthday") if cust else None
    anniversary_iso = cust.get("anniversary") if cust else None

    # Load all payments
    sha, bytes_ = _get_file_info(PAYMENTS_PATH)
    if not bytes_:
        return 0.0
    try:
        df = _df_from_excel_bytes(bytes_)
    except Exception:
        return 0.0

    if df.empty:
        return 0.0

    df = df[df["phone"].astype(str) == str(phone)].copy()
    if df.empty:
        return 0.0

    total = 0.0
    for _, row in df.iterrows():
        amt = float(row.get("amount", 0) or 0)
        ts = str(row.get("timestamp", ""))
        mult = _multiplier_for_purchase(ts, birthday_iso, anniversary_iso)
        total += amt * BASE_POINTS_PER_CURRENCY * mult
    return float(total)
