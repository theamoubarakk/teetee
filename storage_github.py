# storage_github.py — GitHub + Excel storage (payments, customers, redemptions), rewards-as-discount
import base64
import io
import os
import time
from datetime import datetime, date, timedelta

import pandas as pd
import requests
import streamlit as st

# =========================
# Secrets / Config
# =========================
TOKEN  = st.secrets.get("GITHUB_TOKEN",  os.environ.get("GITHUB_TOKEN"))
OWNER  = st.secrets.get("GITHUB_OWNER",  os.environ.get("GITHUB_OWNER", "user"))
REPO   = st.secrets.get("GITHUB_REPO",   os.environ.get("GITHUB_REPO", "repo"))
BRANCH = st.secrets.get("GITHUB_BRANCH", os.environ.get("GITHUB_BRANCH", "main"))

PAYMENTS_PATH     = st.secrets.get("GITHUB_FILE_PATH",        os.environ.get("GITHUB_FILE_PATH", "payments.xlsx"))
CUSTOMERS_PATH    = st.secrets.get("GITHUB_CUSTOMERS_PATH",   os.environ.get("GITHUB_CUSTOMERS_PATH", "customers.xlsx"))
REDEMPTIONS_PATH  = st.secrets.get("GITHUB_REDEMPTIONS_PATH", os.environ.get("GITHUB_REDEMPTIONS_PATH", "redemptions.xlsx"))

API_BASE = "https://api.github.com"

# Rewards tiers (points_cost -> $off). Admin can edit here.
REWARD_TIERS = [(100, 5), (250, 15), (500, 40)]

# Loyalty config
BASE_POINTS_PER_CURRENCY = 1.0
WINDOW_DAYS = 7
BIRTHDAY_POST_WINDOW_DAYS = 7
DISCOUNT_RATE = 0.15
EXPIRY_DAYS = 365

# =========================
# GitHub helpers
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
    hint = []
    if r.status_code == 404:
        hint.append("404 Not Found — token lacks repo/branch access or OWNER/REPO/BRANCH incorrect.")
        hint.append(f"OWNER={OWNER}, REPO={REPO}, BRANCH={BRANCH}, PATH={path}")
        hint.append("For fine-grained tokens: enable 'Contents: Read and write' and grant access to this repo.")
    elif r.status_code == 422:
        hint.append("422 — branch may not exist, or SHA mismatch for update.")
        hint.append(f"Check branch '{BRANCH}' exists and path '{path}' is correct.")
    raise RuntimeError(f"GitHub PUT {path} failed: {r.status_code} {r.text}\n" + "\n".join(map(str, hint)))

# =========================
# Excel helpers
# =========================
def _excel_bytes_from_df(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

def _df_from_excel_bytes(b: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(b), keep_default_na=False)

# =========================
# Birthday normalization helpers
# =========================
def _normalize_birthday_in(value) -> str:
    if value in (None, "", "nan", "NaN"):
        return ""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    v = str(value).strip()
    if not v or v.lower() == "nan":
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return ""

def _normalize_birthday_out(value) -> str | None:
    if value in (None, "", "nan", "NaN"):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    v = str(value).strip()
    if not v or v.lower() == "nan":
        return None
    try:
        return datetime.strptime(v[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None

# =========================
# Customers
# =========================
def get_customer(phone: str) -> dict | None:
    """Retry a few times to avoid read-after-write race from GitHub."""
    attempts = 0
    while attempts < 3:
        attempts += 1
        _, bytes_ = _get_file_info(CUSTOMERS_PATH)
        if not bytes_:
            if attempts < 3:
                time.sleep(0.35)
                continue
            return None
        try:
            df = _df_from_excel_bytes(bytes_)
        except Exception:
            if attempts < 3:
                time.sleep(0.35)
                continue
            return None
        if "phone" not in df.columns:
            return None
        row = df[df["phone"].astype(str) == str(phone)]
        if row.empty:
            if attempts < 3:
                time.sleep(0.35)
                continue
            return None
        r = row.iloc[0]
        birthday_clean = _normalize_birthday_out(r.get("birthday", None))
        total_pts_raw = r.get("total_points", 0)
        return {
            "phone": str(r.get("phone", "")),
            "birthday": birthday_clean,
            "total_points": float(total_pts_raw or 0),
        }
    return None

def save_or_update_customer(phone: str, birthday_iso: str):
    birthday_iso = _normalize_birthday_in(birthday_iso)
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
            df.loc[mask, "birthday"] = birthday_iso or ""
        else:
            df = pd.concat([df, pd.DataFrame([{
                "phone": phone_str, "birthday": birthday_iso or "", "total_points": 0.0
            }])], ignore_index=True)
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
                "phone": phone_str, "birthday": "", "total_points": float(total_points)
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
# Payments / Redemptions (unchanged)
# =========================
def save_payment(phone, original_amount, birthday_discount, reward_discount, points_redeemed, final_amount, method, ts):
    new_row = {
        "phone": str(phone),
        "original_amount": round(float(original_amount), 2),
        "birthday_discount": round(float(birthday_discount), 2),
        "reward_discount": round(float(reward_discount), 2),
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
                    "phone","original_amount","birthday_discount",
                    "reward_discount","points_redeemed","final_amount","method","timestamp"
                ])
        else:
            df = pd.DataFrame(columns=[
                "phone","original_amount","birthday_discount",
                "reward_discount","points_redeemed","final_amount","method","timestamp"
            ])
        if "reward_discount" not in df.columns:
            df["reward_discount"] = 0.0
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

def record_redemption(phone: str, points: float, ts: str):
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
# Loyalty (unchanged logic)
# =========================
def _parse_iso_date_only(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None

def _parse_ts_to_date(ts) -> date:
    try:
        if isinstance(ts, datetime):
            return ts.date()
        if isinstance(ts, date):
            return ts
        s = str(ts)
        s19 = s[:19]
        try:
            return datetime.fromisoformat(s19.replace(" ", "T")).date()
        except Exception:
            return date.fromisoformat(s[:10])
    except Exception:
        return date.today()

def _safe_event_date(year: int, bday: date) -> date:
    try:
        return date(year, bday.month, bday.day)
    except ValueError:
        if bday.month == 2 and bday.day == 29:
            return date(year, 2, 28)
        raise

def _in_birthday_window(purchase_dt: date, bday: date) -> bool:
    this_year_event = _safe_event_date(purchase_dt.year, bday)
    if purchase_dt > this_year_event + timedelta(days=BIRTHDAY_POST_WINDOW_DAYS):
        event = _safe_event_date(purchase_dt.year + 1, bday)
    else:
        event = this_year_event
    start_window = event - timedelta(days=WINDOW_DAYS)
    end_window   = event + timedelta(days=BIRTHDAY_POST_WINDOW_DAYS)
    return start_window <= purchase_dt <= end_window

def apply_birthday_discount(phone: str, amount: float, ts: str) -> tuple[float, float]:
    cust = get_customer(phone)
    bday = _parse_iso_date_only(cust.get("birthday") if cust else None)
    discount_applied = 0.0
    p_dt = _parse_ts_to_date(ts)
    if bday and _in_birthday_window(p_dt, bday):
        discount_applied = amount * DISCOUNT_RATE
        amount -= discount_applied
    return round(amount, 2), round(discount_applied, 2)

def calculate_points_for_amount(original_amount: float) -> float:
    return float(original_amount) * BASE_POINTS_PER_CURRENCY

def calculate_total_points(phone: str, ref_ts: str) -> float:
    ref_date = _parse_ts_to_date(ref_ts)
    cutoff = ref_date - timedelta(days=EXPIRY_DAYS)
    p_df = _load_payments_df()
    if not p_df.empty:
        p_df = p_df[p_df["phone"].astype(str) == str(phone)].copy()
        p_df["date"] = p_df["timestamp"].apply(_parse_ts_to_date)
        p_df = p_df[p_df["date"] >= cutoff]
        earned = float(p_df.get("original_amount", 0).sum()) * BASE_POINTS_PER_CURRENCY
    else:
        earned = 0.0
    r_df = _load_redemptions_df()
    if not r_df.empty:
        r_df = r_df[r_df["phone"].astype(str) == str(phone)].copy()
        r_df["date"] = r_df["timestamp"].apply(_parse_ts_to_date)
        r_df = r_df[r_df["date"] >= cutoff]
        redeemed = float(r_df.get("points", 0).sum())
    else:
        redeemed = 0.0
    balance = max(0.0, earned - redeemed)
    return round(balance, 2)

# =========================
# Admin clear helpers (unchanged)
# =========================
def _reset_excel(path: str, columns: list[str]) -> None:
    df = pd.DataFrame(columns=columns)
    content = _excel_bytes_from_df(df)
    sha, _ = _get_file_info(path)
    _commit_file(path, content, f"Reset {os.path.basename(path)} (clear all data)", sha=sha)

def clear_all_data(include_vouchers: bool = True) -> dict:
    results = {}
    try:
        _reset_excel(CUSTOMERS_PATH, ["phone", "birthday", "total_points"])
        results[os.path.basename(CUSTOMERS_PATH)] = "ok"
    except Exception as e:
        results[os.path.basename(CUSTOMERS_PATH)] = f"error: {e}"
    try:
        _reset_excel(PAYMENTS_PATH, ["phone","original_amount","birthday_discount","reward_discount","points_redeemed","final_amount","method","timestamp"])
        results[os.path.basename(PAYMENTS_PATH)] = "ok"
    except Exception as e:
        results[os.path.basename(PAYMENTS_PATH)] = f"error: {e}"
    try:
        _reset_excel(REDEMPTIONS_PATH, ["phone","points","timestamp"])
        results[os.path.basename(REDEMPTIONS_PATH)] = "ok"
    except Exception as e:
        results[os.path.basename(REDEMPTIONS_PATH)] = f"error: {e}"
    if include_vouchers:
        vouchers_path = st.secrets.get("GITHUB_VOUCHERS_PATH", os.environ.get("GITHUB_VOUCHERS_PATH", "vouchers.xlsx"))
        try:
            _reset_excel(vouchers_path, ["voucher_code","phone","value","issued_ts","redeemed_ts"])
            results[os.path.basename(vouchers_path)] = "ok"
        except Exception as e:
            results[os.path.basename(vouchers_path)] = f"error: {e}"
    return results
