from deps import st, re, datetime
import storage_github as storage

# ---- session init ----
if "phone_valid" not in st.session_state:
    st.session_state["phone_valid"] = False
if "phone" not in st.session_state:
    st.session_state["phone"] = ""
if "profile_saved" not in st.session_state:
    st.session_state["profile_saved"] = False

st.title("Payment Entry + Loyalty")

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

# ---- Step 1.5: customer profile (birthday/anniversary) ----
customer = None
if st.session_state["phone_valid"]:
    try:
        customer = storage.get_customer(st.session_state["phone"])
    except Exception as e:
        st.error(f"Failed to load customer profile: {e}")

    if customer is None:
        st.info("New customer detected. Please add a birthday and optionally an anniversary.")
        dob = st.date_input("Birthday (required)")
        anniv = st.date_input("Anniversary (optional)", value=None)

        if st.button("Save Profile"):
            if dob is None:
                st.error("Birthday is required.")
            else:
                try:
                    storage.save_or_update_customer(
                        phone=st.session_state["phone"],
                        birthday_iso=dob.isoformat(),
                        anniversary_iso=(anniv.isoformat() if anniv else None),
                    )
                    st.session_state["profile_saved"] = True
                    st.success("Customer profile saved.")
                except Exception as e:
                    st.error(f"Failed to save profile: {e}")
    else:
        # show existing profile (read-only)
        st.caption(
            f"Profile → Birthday: {customer.get('birthday','-')}, "
            f"Anniversary: {customer.get('anniversary','-')}"
        )

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
                # Save payment and push to GitHub
                storage.save_payment(
                    phone=st.session_state["phone"],
                    amount=float(amount),
                    method=method,
                    ts=ts,
                )
                # Compute loyalty points for this transaction and total-to-date
                earned, multiplier = storage.calculate_points_for_amount(
                    phone=st.session_state["phone"],
                    amount=float(amount),
                    ts=ts,
                )
                total_points = storage.calculate_total_points(st.session_state["phone"])

                st.success(
                    f"Recorded ${amount:.2f} ({method}) for {st.session_state['phone']} and pushed to GitHub."
                )
                st.info(
                    f"Loyalty: earned {earned:.2f} points (×{multiplier:.1f}); "
                    f"total balance: {total_points:.2f} points."
                )
            except Exception as e:
                st.error(f"Failed to save to GitHub or compute points: {e}")
