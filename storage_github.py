# storage_github.py  — GitHub + Excel storage for payments, customers, redemptions, vouchers
import base64
import io
import os
import time
from datetime import datetime, date, timedelta

import pandas as pd
import requests
import streamlit as st

# =========================
# Configuration (from secrets; env fallback for local dev)
# =========================
TOKEN  = st.secrets.get("GITHUB_TOKEN",  os.environ.get("GITHUB_TOKEN"))
OWNER  = st.secrets.get("GITHUB_OWNER",  os.environ.get("GITHUB_OWNER", "user"))
REPO   = st.secrets.get("GITHUB_REPO",   os.environ.get("GITHUB_REPO", "repo"))
BRANCH = st.secrets.get("GITHUB_BRANCH", os.environ.get("GITHUB_BRANCH", "main"))

PAYMENTS_PATH     = st.secrets.get("GITHUB_FILE_PATH",        os.environ.get("GITHUB_FILE_PATH", "payments.xlsx"))
CUSTOMERS_PATH    = st.secrets.get("GITHUB_CUSTOMERS_PATH",   os.environ.get("GITHUB_CUSTOMERS_PATH", "customers.xlsx"))
REDEMPTIONS_PATH  = st.secrets.get("GITHUB_REDEMPTIONS_PATH", os.environ.get("GITHUB_REDEMPTIONS_PATH", "redemptions.xlsx"))
VOUCHERS_PATH     = st.secrets.get("GITHUB_VOUCHERS_PATH",    os.environ.get("GITHUB_VOUCHERS_PATH", "vouchers.xlsx"))

API_BASE = "https://api.github.com"

# =========================
# Rewards config (edit tiers here)
# =========================
# (points_cost, voucher_amount)
REWARD_TIERS = [(100, 5), (250, 15), (500, 40)]

# =========================
# GitHub helpers (Contents API)
# =========================
def _headers():
    if not TOKEN:
        raise RuntimeError("Missing GITHUB_TOKEN in Streamlit secrets.")
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def _contents_url(path: str) -> str:
    return f"{API_BASE}/repos/{OWNER}/{REPO}/contents/{path}"

def _get_file_info(path: str):
    """
    Return (sha, raw_bytes) for file at path on BRANCH.
    If file not found -> (None, None).
    """
    r = requests.get(_contents_url(path), headers=_headers(), params={"ref": BRANCH})
    if r.status_code == 200:
        data = r.json()
        try:
            content_bytes = base64.b64decode(data["content"])
        except Exception:
            content_bytes = None
        return data.get("sha"), content_bytes
    if r.status_code == 404:
        return None, None
    raise RuntimeError(f"GitHub GET {path} failed: {r.status_code} {r.text}")

def _commit_file(path: str, content_bytes: bytes, message: str, sha: str | None):
    """
    Create or update file at path on BRANCH using the Contents API.
    Raises with helpful hints on failure.
    """
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(_contents_url(path), headers=_headers(), json=payload)
    if r.status_code in (200, 201):
        return

    # Diagnostics
    hint = []
    if r.status_code == 404:
        hint.append("404 Not Found from GitHub Contents API.")
        hint.append("This usually means your token lacks access to the repo/branch,")
        hint.append("or OWNER/REPO/BRANCH is wrong.")
        hint.append(f"OWNER={OWNER}, REPO={REPO}, BRANCH={BRANCH}, PATH={path}")
        hint.append("For fine-grained tokens: enable 'Contents: Read and write' and grant access to this repo.")
    elif r.status_code == 422:
        hint.append("422 Unprocessable Entity. Often the branch name does not exist,")
        hint.append("or the provided sha is wrong for updates.")
        hint.append(f"Check BRANCH '{BRANCH}' exists and PATH '{path}' is valid.")
    else:
        hint.append(f"GitHub returned {r.status_code}.")
    raise RuntimeError(f"GitHub PUT {path} failed: {r.status_code} {r.text}\n" + "\n".join(hint))

def github_preflight() -> str:
    """Optional quick checks; return '' if OK, otherwise a human-friendly message."""
    try:
        rr = requests.get(f"{API_BASE}/repos/{OWNER}/{REPO}", headers=_headers())
        if rr.status_code != 200:
            return f"Cannot access repo {OWNER}/{REPO} (status {rr.status_code})."
        br = requests.get(f"{API_BASE}/repos/{OWNER}/{REPO}/branches/{BRANCH}", headers=_headers())
        if br.status_code != 200:
            return f"Branch '{BRANCH}' not found (status {br.status_code})."
        return ""
    except Exception as e:
        return f"Preflight error: {e}"

# =========================
# Excel helpers
# =========================
def _excel_bytes_from_df(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

def _df_from_excel_bytes(b: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(b))

# =========================
# Customers
# =========================
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
        "total_points": float(r.get("total_points", 0) or 0),
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

        if "total_points" not in df.columns:
            df["total_points"] = 0.0

        phone_str = str(phone)
        mask = (df["phone"].astype(str) == phone_str)
        if mask.any():
            df.loc[mask, "birthday"] = birthday_iso
        else:
            df = pd.concat(
                [df, pd.DataFrame([{"phone": phone_str, "birthday": birthday_iso, "total_points": 0.0}])],
                ignore_index=True
            )

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
    """
    Persist latest computed points into customers.xlsx (creates file/column if missing).
    """
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

        updated_bytes = _excel_bytes_from_df(df)
        try:
            _commit_file(CUSTOMERS_PATH, updated_bytes, f"Update points {phone_str} -> {total_points:.2f}", sha=sha)
            return
        except RuntimeError as e:
            if "409" in str(e) and attempts < 3:
                time.sleep(0.8)
                continue
            raise

def get_customers_file_bytes() -> bytes | None:
    _, bytes_ = _get_file_info(CUSTOMERS_PATH)
    return bytes_

# =========================
# Payments
# =========================
def save_payment(
    phone: str,
    original_amount: float,
    birthday_discount: float,
    points_redeemed: float,
    final_amount: float,
    method: str,
    ts: str,
) -> None:
    """
    Append one payment row to payments.xlsx with full breakdown.
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
                    "phone", "original_amount", "birthday_discount",
                    "points_redeemed", "final_amount", "method", "timestamp"
                ])
        else:
            df = pd.DataFrame(columns=[
                "phone", "original_amount", "birthday_discount",
                "points_redeemed", "final_amount", "method", "timestamp"
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

def _load_payments_df() -> pd.DataFrame:
    _, bytes_ = _get_file_info(PAYMENTS_PATH)
    if not bytes_:
        return pd.DataFrame(columns=["phone", "original_amount", "timestamp"])
    try:
        return _df_from_excel_bytes(bytes_)
    except Exception:
        return pd.DataFrame(columns=["phone", "original_amount", "timestamp"])

def get_payments_file_bytes() -> bytes | None:
    _, bytes_ = _get_file_info(PAYMENTS_PATH)
    return bytes_

# =========================
# Redemptions (points spent)
# =========================
def record_redemption(phone: str, points: float, ts: str):
    """
    Append a redemption (points spent) to redemptions.xlsx.
    """
    new_row = {"phone": str(phone), "points": round(float(points), 2), "timestamp": ts}

    attempts = 0
    while True:
        attempts += 1
        sha, bytes_ = _get_file_info(REDEMPTIONS_PATH)
        if bytes_:
            try:
                df = _df_from_excel_bytes(bytes_)
            except Exception:
                df = pd.DataFrame(columns=["phone", "points", "timestamp"])
        else:
            df = pd.DataFrame(columns=["phone", "points", "timestamp"])

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

def _load_redemptions_df() -> pd.DataFrame:
    _, bytes_ = _get_file_info(REDEMPTIONS_PATH)
    if not bytes_:
        return pd.DataFrame(columns=["phone", "points", "timestamp"])
    try:
        return _df_from_excel_bytes(bytes_)
    except Exception:
        return pd.DataFrame(columns=["phone", "points", "timestamp"])

# =========================
# Loyalty: earning, expiry, birthday discount
# =========================
BASE_POINTS_PER_CURRENCY = 1.0   # 1 point per 1 currency unit
WINDOW_DAYS = 7                  # pre-birthday discount window (days before)
DISCOUNT_RATE = 0.15             # 15% birthday discount
EXPIRY_DAYS = 365                # points expire after 1 year

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
    """
    Return (final_amount_after_discount, discount_applied).
    Discount applies if purchase is within WINDOW_DAYS **before** birthday.
    """
    cust = get_customer(phone)
    bday = _parse_iso_date_only(cust.get("birthday") if cust else None)
    discount_applied = 0.0
    p_dt = _parse_ts_to_date(ts)

    if bday and _in_pre_birthday_window(p_dt, bday):
        discount_applied = amount * DISCOUNT_RATE
        amount -= discount_applied

    return round(amount, 2), round(discount_applied, 2)

def calculate_points_for_amount(original_amount: float) -> float:
    """1 point per $1 on ORIGINAL amount (pre-discount)."""
    return float(original_amount) * BASE_POINTS_PER_CURRENCY

def calculate_total_points(phone: str, ref_ts: str) -> float:
    """
    Compute unexpired points balance at reference timestamp:
      balance = sum(earned within last 365 days) - sum(redeemed within last 365 days)
      earned   -> from payments.xlsx (original_amount)
      redeemed -> from redemptions.xlsx (points)
    """
    ref_date = _parse_ts_to_date(ref_ts)
    cutoff = ref_date - timedelta(days=EXPIRY_DAYS)

    # Earned
    p_df = _load_payments_df()
    if not p_df.empty:
        p_df = p_df[p_df["phone"].astype(str) == str(phone)].copy()
        p_df["date"] = p_df["timestamp"].astype(str).str[:19].apply(_parse_ts_to_date)
        p_df = p_df[p_df["date"] >= cutoff]
        earned = float(p_df.get("original_amount", 0).sum()) * BASE_POINTS_PER_CURRENCY
    else:
        earned = 0.0

    # Redeemed
    r_df = _load_redemptions_df()
    if not r_df.empty:
        r_df = r_df[r_df["phone"].astype(str) == str(phone)].copy()
        r_df["date"] = r_df["timestamp"].astype(str).str[:19].apply(_parse_ts_to_date)
        r_df = r_df[r_df["date"] >= cutoff]
        redeemed = float(r_df.get("points", 0).sum())
    else:
        redeemed = 0.0

    balance = max(0.0, earned - redeemed)
    return round(balance, 2)

# =========================
# Vouchers (rewards)
# =========================
def _load_vouchers_df() -> pd.DataFrame:
    _, b = _get_file_info(VOUCHERS_PATH)
    if not b:
        return pd.DataFrame(columns=["phone","code","points_cost","amount","issued_ts","redeemed","redeemed_ts"])
    try:
        return _df_from_excel_bytes(b)
    except Exception:
        return pd.DataFrame(columns=["phone","code","points_cost","amount","issued_ts","redeemed","redeemed_ts"])

def _save_vouchers_df(df: pd.DataFrame):
    sha, _ = _get_file_info(VOUCHERS_PATH)
    bytes_ = _excel_bytes_from_df(df)
    _commit_file(VOUCHERS_PATH, bytes_, "Update vouchers ledger", sha=sha)

def _generate_code(phone: str, points_cost: int, amount: float, ts: str) -> str:
    last4 = str(phone)[-4:]
    stamp = datetime.fromisoformat(ts[:19]).strftime("%Y%m%d%H%M%S")
    return f"V{last4}-{points_cost}-{int(amount)}-{stamp}"

def list_vouchers(phone: str) -> pd.DataFrame:
    df = _load_vouchers_df()
    if df.empty:
        return df
    return df[df["phone"].astype(str) == str(phone)].sort_values("issued_ts", ascending=False)

def issue_voucher(phone: str, points_cost: int, amount: float) -> str:
    """
    Create a voucher for this phone (deducts points immediately via redemptions.xlsx).
    Returns the voucher code.
    """
    ts = datetime.now().isoformat(timespec="seconds")

    # Deduct points first (so balance remains accurate)
    record_redemption(phone=phone, points=float(points_cost), ts=ts)

    # Append voucher row
    df = _load_vouchers_df()
    code = _generate_code(phone, points_cost, amount, ts)
    row = {
        "phone": str(phone),
        "code": code,
        "points_cost": int(points_cost),
        "amount": float(amount),
        "issued_ts": ts,
        "redeemed": False,
        "redeemed_ts": None,
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _save_vouchers_df(df)
    return code

def redeem_voucher(code: str) -> tuple[bool, int]:
    """
    Mark a voucher as redeemed by code. Returns (success, points_cost).
    Points were deducted on issue, so no points math here.
    """
    df = _load_vouchers_df()
    if df.empty:
        return False, 0
    mask = (df["code"].astype(str) == str(code))
    if not mask.any():
        return False, 0
    if bool(df.loc[mask].iloc[0].get("redeemed")):
        return False, 0

    df.loc[mask, "redeemed"] = True
    df.loc[mask, "redeemed_ts"] = datetime.now().isoformat(timespec="seconds")
    points_cost = int(df.loc[mask].iloc[0]["points_cost"])
    _save_vouchers_df(df)
    return True, points_cost
