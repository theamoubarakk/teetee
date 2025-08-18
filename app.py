from deps import st, re, datetime
from datetime import date
import storage_github as storage

st.title("Payment Entry + Loyalty (Points + Rewards)")

# ---- session init ----
if "phone_valid" not in st.session_state:
    st.session_state["phone_valid"] = False
if "phone" not in st.session_state:
    st.session_state["phone"] = ""
if "profile_saved" not in st.session_state:
    st.session_state["profile_saved"] = False

# ---- Admin mode ----
admin_mode = st.sidebar.checkbox(
    "Admin mode",
    value=True,
    help="Staff tools: view eligibility, issue vouchers, and manage codes."
)

# ---- Step 1: phone capture ----
phone = st.text_input(
    "Enter your phone number (exactly 8 digits):",
    value=st.session_state["phone"]
)
if st.button("Next"):
    if re.fullmatch(r"\d{8}", phone):
        st.session_state["phone_valid"] = True
        st.session_state["phone"] = phone
    else:
        st.session_state["phone_valid"] = False
        st.error("Invalid phone number. Please enter exactly 8 digits (0–9).")

# ---- Step 1.5: profile (birthday saved once; used elsewhere if needed) ----
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

# ---- Step 2: payment entry (earn points; no auto-redeem) ----
if st.session_state["phone_valid"]:
    amount = st.number_input(
        "Enter payment amount:",
        min_value=0.01,
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

                # Current unexpired balance
                current_points = storage.calculate_total_points(st.session_state["phone"], ts)
                storage.update_customer_points(st.session_state["phone"], current_points)

                # Save payment (earn 1 point per $1 on original amount)
                storage.save_payment(
                    phone=st.session_state["phone"],
                    original_amount=float(amount),
                    birthday_discount=0.0,
                    points_redeemed=0.0,
                    final_amount=float(amount),
                    method=method,
                    ts=ts,
                )

                # Points earned this purchase
                earned = storage.calculate_points_for_amount(float(amount))

                # Local balance update (avoid GitHub read-after-write lag)
                new_balance = current_points + earned
                storage.update_customer_points(st.session_state["phone"], new_balance)

                # Minimal blue box
                st.info(f"earned points: {earned:.2f}\n\ntotal points: {new_balance:.2f}")

            except Exception as e:
                st.error(f"Failed to process payment: {e}")

# ---- Rewards (Admin) ----
if st.session_state.get("phone_valid") and admin_mode:
    st.subheader("Rewards (Admin)")

    # Latest balance
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        balance = storage.calculate_total_points(st.session_state["phone"], ts)
        storage.update_customer_points(st.session_state["phone"], balance)
    except Exception as e:
        st.error(f"Failed to compute balance: {e}")
        balance = 0.0

    st.write(f"**Current balance:** {balance:.2f} points")

    # Eligibility + issue buttons
    with st.expander("Issue Voucher"):
        c1, c2 = st.columns(2)
        with c1:
            st.caption("Tiers (1 point = $1):")
            for cost, cash in storage.REWARD_TIERS:
                eligible = balance >= cost
                st.write(("✅ " if eligible else "❌ ") + f"{cost} points → ${cash} voucher")
        with c2:
            st.caption("Actions:")
            for cost, cash in storage.REWARD_TIERS:
                eligible = balance >= cost
                if st.button(f"Issue ${cash} (cost {cost} pts)", disabled=not eligible, key=f"issue_{cost}"):
                    try:
                        code = storage.issue_voucher(
                            phone=st.session_state["phone"],
                            points_cost=cost,
                            amount=cash,
                        )
                        balance = max(0.0, balance - cost)
                        storage.update_customer_points(st.session_state["phone"], balance)
                        st.info(f"Issued voucher {code} for ${cash}. New balance: {balance:.2f} pts.")
                    except Exception as e:
                        st.error(f"Failed to issue voucher: {e}")

    # Voucher list + redeem
    with st.expander("Vouchers for this customer"):
        try:
            vdf = storage.list_vouchers(st.session_state["phone"])
        except Exception as e:
            vdf = None
            st.error(f"Failed to load vouchers: {e}")

        if vdf is None or vdf.empty:
            st.caption("No vouchers yet.")
        else:
            st.dataframe(vdf)
            code_to_redeem = st.text_input("Enter voucher code to mark as redeemed")
            if st.button("Mark Redeemed"):
                try:
                    ok, _ = storage.redeem_voucher(code_to_redeem)
                    if ok:
                        st.info(f"Voucher {code_to_redeem} marked redeemed.")
                    else:
                        st.error("Voucher code not found or already redeemed.")
                except Exception as e:
                    st.error(f"Failed to redeem voucher: {e}")

# ---- Download customers.xlsx (guarded; won’t crash if secrets missing) ----
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
