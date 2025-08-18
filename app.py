from deps import st, re, datetime
from datetime import date
import storage_github as storage

# ---- session init ----
if "phone_valid" not in st.session_state:
    st.session_state["phone_valid"] = False
if "phone" not in st.session_state:
    st.session_state["phone"] = ""
if "profile_saved" not in st.session_state:
    st.session_state["profile_saved"] = False

st.title("Payment Entry + Loyalty (Birthday Discount + Auto-Points)")

# ---- Step 1: phone capture ----
phone = st.text_input(
    "Enter your phone number (exactly 8 digits):",
    value=st.session_state["phone"]
)

if st.button("Next"):
    if re.fullmatch(r"\d{8}", phone):
        st.session_state["phone_valid"] = True
        st.session_state["phone"] = phone
        # (no st.success -> no green box)
    else:
        st.session_state["phone_valid"] = False
        st.error("Invalid phone number. Please enter exactly 8 digits (0–9).")

# ---- Step 1.5: customer profile (birthday only) ----
customer = None
if st.session_state["phone_valid"]:
    try:
        customer = storage.get_customer(st.session_state["phone"])
    except Exception as e:
        st.error(f"Failed to load customer profile: {e}")

    if customer is None:
        st.info("New customer detected. Please add a birthday.")
        dob = st.date_input(
            "Birthday (required)",
            min_value=date(1960, 1, 1),
            max_value=date.today()
        )

        if st.button("Save Profile"):
            if dob is None:
                st.error("Birthday is required.")
            else:
                try:
                    storage.save_or_update_customer(
                        phone=st.session_state["phone"],
                        birthday_iso=dob.isoformat(),
                    )
                    storage.update_customer_points(st.session_state["phone"], 0.0)
                    st.session_state["profile_saved"] = True
                except Exception as e:
                    st.error(f"Failed to save profile: {e}")
    else:
        st.caption(f"Profile → Birthday: {customer.get('birthday','-')}")

# ---- Step 2: payment (auto birthday discount + auto points redemption) ----
if st.session_state["phone_valid"]:
    amount = st.number_input(
        "Enter payment amount:",
        min_value=0.01,      # strictly > 0
        step=0.01,
        format="%.2f"
    )
    method = st.selectbox("Payment Method", ["Cash", "Check", "Credit Card"])

    if st.button("Submit Payment"):
        if amount <= 0:
            st.error("Amount must be greater than 0.")
        else:
            try:
                ts = datetime.now().isoformat(timespec="seconds")

                # 1) 15% birthday discount if within 7 days before birthday
                after_bday, bday_discount = storage.apply_birthday_discount(
                    phone=st.session_state["phone"],
                    amount=float(amount),
                    ts=ts,
                )

                # 2) Auto-redeem unexpired points, up to amount due
                current_points = storage.calculate_total_points(st.session_state["phone"], ts)
                storage.update_customer_points(st.session_state["phone"], current_points)
                points_to_redeem = max(0.0, min(current_points, after_bday))
                final_amount = round(after_bday - points_to_redeem, 2)

                # 3) Save payment with full breakdown
                storage.save_payment(
                    phone=st.session_state["phone"],
                    original_amount=float(amount),
                    birthday_discount=bday_discount,
                    points_redeemed=points_to_redeem,
                    final_amount=final_amount,
                    method=method,
                    ts=ts,
                )

                # 4) Record redemption (deduct balance)
                if points_to_redeem > 0:
                    storage.record_redemption(
                        phone=st.session_state["phone"],
                        points=points_to_redeem,
                        ts=ts,
                    )

                # 5) Points earned on ORIGINAL amount
                earned = storage.calculate_points_for_amount(float(amount))

                # 6) Recompute balance (expiry & redemptions) and persist
                new_balance = storage.calculate_total_points(st.session_state["phone"], ts)
                storage.update_customer_points(st.session_state["phone"], new_balance)

                # ---- Only the blue box (two lines)
                st.info(f"earned points: {earned:.2f}\n\ntotal points: {new_balance:.2f}")

            except Exception as e:
                st.error(f"Failed to process payment: {e}")

# ---- Download customers.xlsx (SAFE: only if secrets exist; otherwise no call) ----
required = ["GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "GITHUB_BRANCH", "GITHUB_CUSTOMERS_PATH"]
if all(k in st.secrets for k in required):
    try:
        cust_bytes = storage.get_customers_file_bytes()
    except Exception:
        cust_bytes = None
    if cust_bytes:
        st.download_button(
            label="Download Customers Excel",
            data=cust_bytes,
            file_name="customers.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="Download the latest customers.xlsx (includes total points per phone)"
        )
    else:
        st.caption("Customers Excel not available yet.")
else:
    st.caption("Download disabled: add GitHub secrets in Streamlit → Settings → Secrets.")
