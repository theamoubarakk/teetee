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

PAYMENTS_PATH     = st.secrets.get("GITHUB_FILE_PATH",       os.environ.get("GITHUB_FILE_PATH", "payments.xlsx"))
CUSTOMERS_PATH    = st.secrets.get("GITHUB_CUSTOMERS_PATH",  os.environ.get("GITHUB_CUSTOMERS_PATH", "customers.xlsx"))
REDEMPTIONS_PATH  = st.secrets.get("GITHUB_REDEMPTIONS_PATH",os.environ.get("GITHUB_REDEMPTIONS_PATH", "redemptions.xlsx"))

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
    _, bytes_ = _get_file_info(CUSTOMERS_PATH)
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
        "total_points": float(r.get("total_points", 0) or 0)
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
                df = pd.DataFrame(columns=["phone", "birthday", "total_points"])
        else:
            df = pd.DataFrame(columns=["phone", "birthday", "total_points"])

        phone_str = str(phone)
        if "total_points" not in df.columns:
            df["total_points"] = 0.0

        mask = df["phone"].astype(str) == phone_str
        if mask.any():
            df.loc[mask, "birthday"] = birthday_iso
        else:
            df = pd.concat([
                df,
                pd.DataFrame([{"phone": phone_str, "birthday": birthday_iso, "total_points": 0.0}])
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

def update_customer_points(phone: str, total_points: float):
    """Persist the latest computed points balance into customers.xlsx."""
    attempts = 0
    while True:
        attempts += 1
        sha, bytes_ = _get_file_info(CUSTOMERS_PATH)
        if bytes_:
            try:
                df = _df_from_excel_bytes(bytes_)
            except Exception:
                df = pd.DataFrame(columns=["phone", "birthday", "total_points"])
        else:
            df = pd.DataFrame(columns=["phone", "birthday", "total_points"])

        if "total_points" not in df.columns:
            df["total_points"] = 0.0

        phone_str = str(phone)
        mask = (df["phone"].astype(str) == phone_str)
        if mask.any():
            df.loc[mask, "total_points"] = float(total_points)
        else:
            df = pd.concat([df, pd.DataFrame([{
                "phone": phone_str,
                "birthday": None,
                "total_points": float(total_points)
            }])], ignore_index=True)

        updated = _excel_bytes_from_df(df)
        try:
            _commit_file(CUSTOMERS_PATH, updated, f"Update points {phone_str} -> {total_points:.2f}", sha=sha)
            return
        except RuntimeError as e:
            if "409" in str(e) and attempts < 3:
                time.sleep(0.8)
                continue
            raise

# ---------- Payments ----------
def save_payment(phone: str, original_amount: float, birthday_discount: float, points_redeemed: float, final_amount: float, method: str, ts: str) -> None:
    """
    Append one payment row with full details:
      - original_amount (pre-discount)
      - birthday_discount
      - points_redeemed (from balance)
      - final_amount (after all discounts)
    """
    new_row = {
        "phone": str(phone),
        "original_amount": round(float(original_amount), 2),
        "birthday_discount": round(float(birthday_discount), 2),
        "points_redeemed": round(float(points_redeemed), 2),
        "final_amount": round(float(final_amount), 2),
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
                df = pd.DataFrame(columns=[
                    "phone","original_amount","birthday_discount","points_redeemed","final_amount","method","timestamp"
                ])
        else:
            df = pd.DataFrame(columns=[
                "phone","original_amount","birthday_discount","points_redeemed","final_amount","method","timestamp"
            ])

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

# ---------- Redemptions (points spent) ----------
def record_redemption(phone: str, points: float, ts: str):
    """Append a redemption record (points spent)."""
    new_row = {
        "phone": str(phone),
        "points": round(float(points), 2),
        "timestamp": ts
    }
    attempts = 0
    while True:
        attempts += 1
        sha, bytes_ = _get_file_info(REDEMPTIONS_PATH)
        if bytes_:
            try:
                df = _df_from_excel_bytes(bytes_)
            except Exception:
                df = pd.DataFrame(columns=["phone","points","timestamp"])
        else:
            df = pd.DataFrame(columns=["phone","points","timestamp"])

        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        updated = _excel_bytes_from_df(df)
        try:
            _commit_file(REDEMPTIONS_PATH, updated, f"Redeem points {new_row['phone']} {new_row['points']}", sha=sha)
            return
        except RuntimeError as e:
            if "409" in str(e) and attempts < 3:
                time.sleep(0.8)
                continue
            raise

# ---------- Loyalty, Expiry & Discount ----------
BASE_POINTS_PER_CURRENCY = 1.0       # 1 point per 1 currency unit
WINDOW_DAYS = 7                      # pre-birthday discount window
DISCOUNT_RATE = 0.15                 # 15% discount
EXPIRY_DAYS = 365                    # points expire 1 year after earning

def _parse_iso_date_only(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None

def _parse_ts_to_date(ts: str) -> date:
    try:
        return datetime.fromisoformat(ts[:19]).date()
    except Exception:
        return date.today()

def _in_pre_birthday_window(purchase_dt: date, bday: date) -> bool:
    event_this_year = date(purchase_dt.year, bday.month, bday.day)
    start_window = event_this_year - timedelta(days=WINDOW_DAYS)
    return start_window <= purchase_dt < event_this_year

def apply_birthday_discount(phone: str, amount: float, ts: str) -> tuple[float, float]:
    """Return (final_amount, birthday_discount_applied)."""
    cust = get_customer(phone)
    bday = _parse_iso_date_only(cust.get("birthday") if cust else None)
    discount_applied = 0.0
    p_dt = _parse_ts_to_date(ts)

    if bday and _in_pre_birthday_window(p_dt, bday):
        discount_applied = amount * DISCOUNT_RATE
        amount -= discount_applied

    return round(amount, 2), round(discount_applied, 2)

def calculate_points_for_amount(original_amount: float) -> float:
    """Points are based on ORIGINAL (pre-discount) amount."""
    return float(original_amount) * BASE_POINTS_PER_CURRENCY

def _load_payments_df():
    sha, bytes_ = _get_file_info(PAYMENTS_PATH)
    if not bytes_:
        return pd.DataFrame(columns=["phone","original_amount","timestamp"])
    try:
        df = _df_from_excel_bytes(bytes_)
    except Exception:
        return pd.DataFrame(columns=["phone","original_amount","timestamp"])
    return df

def _load_redemptions_df():
    sha, bytes_ = _get_file_info(REDEMPTIONS_PATH)
    if not bytes_:
        return pd.DataFrame(columns=["phone","points","timestamp"])
    try:
        df = _df_from_excel_bytes(bytes_)
    except Exception:
        return pd.DataFrame(columns=["phone","points","timestamp"])
    return df

def calculate_total_points(phone: str, ref_ts: str) -> float:
    """
    Compute unexpired points balance at reference timestamp:
      balance = sum(earned within last 365 days) - sum(redeemed within last 365 days)
      earned = ORIGINAL amounts from payments
      redeemed = points spent (redemptions.xlsx)
    """
    ref_date = _parse_ts_to_date(ref_ts)

    # Earned points (from payments) within EXPIRY_DAYS
    p_df = _load_payments_df()
    if not p_df.empty:
        p_df = p_df[p_df["phone"].astype(str) == str(phone)].copy()
        p_df["date"] = p_df["timestamp"].astype(str).str[:19].apply(lambda s: _parse_ts_to_date(s))
        cutoff = ref_date - timedelta(days=EXPIRY_DAYS)
        p_df = p_df[p_df["date"] >= cutoff]
        earned = float(p_df.get("original_amount", 0).sum()) * BASE_POINTS_PER_CURRENCY
    else:
        earned = 0.0

    # Redeemed points within EXPIRY_DAYS
    r_df = _load_redemptions_df()
    if not r_df.empty:
        r_df = r_df[r_df["phone"].astype(str) == str(phone)].copy()
        r_df["date"] = r_df["timestamp"].astype(str).str[:19].apply(lambda s: _parse_ts_to_date(s))
        cutoff = ref_date - timedelta(days=EXPIRY_DAYS)
        r_df = r_df[r_df["date"] >= cutoff]
        redeemed = float(r_df.get("points", 0).sum())
    else:
        redeemed = 0.0

    balance = max(0.0, earned - redeemed)
    return round(balance, 2)

# ---------- Download helpers ----------
def get_customers_file_bytes() -> bytes | None:
    _, bytes_ = _get_file_info(CUSTOMERS_PATH)
    return bytes_

