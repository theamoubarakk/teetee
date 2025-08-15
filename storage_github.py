import base64
import io
import os
import time
import requests
import pandas as pd
import streamlit as st

# --- Configuration from Streamlit secrets or env ---
TOKEN     = st.secrets.get("GITHUB_TOKEN",  os.environ.get("GITHUB_TOKEN"))
OWNER     = st.secrets.get("GITHUB_OWNER",  os.environ.get("GITHUB_OWNER", "user"))
REPO      = st.secrets.get("GITHUB_REPO",   os.environ.get("GITHUB_REPO", "your-repo-name"))
BRANCH    = st.secrets.get("GITHUB_BRANCH", os.environ.get("GITHUB_BRANCH", "main"))
FILE_PATH = st.secrets.get("GITHUB_FILE_PATH", os.environ.get("GITHUB_FILE_PATH", "payments.xlsx"))

API_BASE = "https://api.github.com"
CONTENTS_URL = f"{API_BASE}/repos/{OWNER}/{REPO}/contents/{FILE_PATH}"

def _headers():
    if not TOKEN:
        raise RuntimeError(
            "Missing GITHUB_TOKEN. Add it under Streamlit Cloud → App → Settings → Secrets."
        )
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}

def _get_file_info():
    """
    Return (sha, bytes) of the current file on the given branch, or (None, None) if not found.
    """
    params = {"ref": BRANCH}
    r = requests.get(CONTENTS_URL, headers=_headers(), params=params)
    if r.status_code == 200:
        data = r.json()
        content_bytes = base64.b64decode(data["content"])
        return data["sha"], content_bytes
    if r.status_code == 404:
        return None, None
    raise RuntimeError(f"GitHub GET failed: {r.status_code} {r.text}")

def _commit_file(content_bytes: bytes, message: str, sha: str | None):
    """
    Create or update the file via GitHub Contents API.
    """
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(CONTENTS_URL, headers=_headers(), json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT failed: {r.status_code} {r.text}")

def _excel_bytes_from_df(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()

def _df_from_excel_bytes(b: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(b))

def save_payment(phone: str, amount: float, method: str, ts: str) -> None:
    """
    Load the current Excel from GitHub (if exists), append one row, and commit back.
    Includes a small retry loop to avoid occasional 409 conflicts on concurrent writes.
    """
    new_row = {"phone": phone, "amount": round(amount, 2), "method": method, "timestamp": ts}

    attempts = 0
    while True:
        attempts += 1

        sha, existing_bytes = _get_file_info()
        if existing_bytes:
            try:
                df = _df_from_excel_bytes(existing_bytes)
            except Exception:
                df = pd.DataFrame(columns=["phone", "amount", "method", "timestamp"])
        else:
            df = pd.DataFrame(columns=["phone", "amount", "method", "timestamp"])

        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        updated_bytes = _excel_bytes_from_df(df)

        try:
            _commit_file(
                updated_bytes,
                message=f"Add payment {phone} ({method}) {ts}",
                sha=sha
            )
            return  # success
        except RuntimeError as e:
            # Handle 409 conflict by brief backoff + retry.
            if "409" in str(e) and attempts < 3:
                time.sleep(0.8)
                continue
            raise
