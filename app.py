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

st.title("Payment Entry + Loyalty (Birthday Discount)")

# ---- Step 1: phone capture ----
phone = st.text_input(
    "Enter your phone number (exactly 8 digits):",
    value=st.session_state["phone"]
)

if st.button("Next"):
    if re.fullmatch(r"\d{8}", phone):
        st.session_state["phone_valid"] = True
        st.session_state["phone"] = phone
        st.success("Phone number accepted.")
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
                    st.session_state["profile_saved"] = True
                    st.success("Customer profile saved.")
                except Exception as e:
                    st.error(f"Failed to save profile: {e}")
    else:
        # show existing profile (read-only)
        st.caption(f"Profile → Birthday: {customer.get('birthday','-')}")

# ---- Step 2: payment if valid ----
if st.session_state["phone_valid"]:
    amount = st.number_input(
        "Enter payment amount:",
        min_value=0.01,      # requires > 0
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

                # Apply 15% birthday discount if within 7 days before birthday
                final_amount, discount_applied = storage.apply_birthday_discount(
                    phone=st.session_state["phone"],
                    amount=float(amount),
                    ts=ts,
                )

                # Save discounted payment to GitHub
                storage.save_payment(
                    phone=st.session_state["phone"],
                    amount=final_amount,
                    method=method,
                    ts=ts,
                )

                # Compute loyalty points (1 point per $1 of final_amount)
                earned = storage.calculate_points_for_amount(final_amount)
                total_points = storage.calculate_total_points(st.session_state["phone"])

                msg = (
                    f"Recorded ${final_amount:.2f} ({method}) for {st.session_state['phone']} "
                    f"and pushed to GitHub."
                )
                if discount_applied > 0:
                    msg += f" Applied a ${discount_applied:.2f} birthday discount."
                st.success(msg)

                st.info(
                    f"Loyalty: earned {earned:.2f} points; "
                    f"total balance: {total_points:.2f} points."
                )

            except Exception as e:
                st.error(f"Failed to save to GitHub or compute points: {e}")

# ---- Download payments.xlsx (visible anytime) ----
excel_bytes = storage.get_payments_file_bytes()
if excel_bytes:
    st.download_button(
        label="Download Payments Excel",
        data=excel_bytes,
        file_name="payments.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help="Download the latest payments.xlsx from your GitHub repo"
    )
else:
    st.caption("No payments file found yet. Submit a payment to create it.")
