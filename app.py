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

st.title("Payment Entry + Loyalty (Birthday Discount + Points)")

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
available_points = 0.0
now_ts = datetime.now().isoformat(timespec="seconds")

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
                    # Initialize points (0) in customers.xlsx
                    storage.update_customer_points(st.session_state["phone"], 0.0)
                    st.session_state["profile_saved"] = True
                    st.success("Customer profile saved.")
                except Exception as e:
                    st.error(f"Failed to save profile: {e}")
    else:
        # Compute & display up-to-date points (with 1-year expiry)
        try:
            available_points = storage.calculate_total_points(st.session_state["phone"], now_ts)
            # Persist the up-to-date balance into customers.xlsx
            storage.update_customer_points(st.session_state["phone"], available_points)
        except Exception as e:
            st.error(f"Failed to compute or update points: {e}")
        st.caption(
            f"Profile → Birthday: {customer.get('birthday','-')} | "
            f"Available Points: {available_points:.2f}"
        )

# ---- Step 2: payment + optional redemption ----
if st.session_state["phone_valid"]:
    amount = st.number_input(
        "Enter payment amount:",
        min_value=0.01,      # requires > 0
        step=0.01,
        format="%.2f"
    )
    method = st.selectbox("Payment Method", ["Cash", "Check", "Credit Card"])

    # Allow redeeming points (whole currency units recommended)
    # Max you can redeem is the smaller of available_points and the amount (after birthday discount will be applied later;
    # we still cap the requested redemption to amount to avoid negative final).
    max_redeem_hint = f"(You can redeem up to {available_points:.0f} points)"
    redeem_points = st.number_input(
        f"Use points as discount {max_redeem_hint}",
        min_value=0.0,
        step=1.0,
        value=0.0
    )

    if st.button("Submit Payment"):
        if amount <= 0:
            st.error("Amount must be greater than 0.")
        else:
            try:
                ts = datetime.now().isoformat(timespec="seconds")

                # 1) Apply 15% birthday discount if within 7 days before birthday
                final_amount, birthday_discount = storage.apply_birthday_discount(
                    phone=st.session_state["phone"],
                    amount=float(amount),
                    ts=ts,
                )

                # 2) Cap redemption by available unexpired points and by final_amount
                #    (points are $1 per point)
                current_points = storage.calculate_total_points(st.session_state["phone"], ts)
                storage.update_customer_points(st.session_state["phone"], current_points)
                max_redeem_allowed = max(0.0, min(current_points, final_amount))
                points_to_redeem = min(float(redeem_points), max_redeem_allowed)

                # Apply redemption to final amount
                final_after_redemption = round(final_amount - points_to_redeem, 2)

                # 3) Save payment with full breakdown; points are earned on ORIGINAL amount only
                storage.save_payment(
                    phone=st.session_state["phone"],
                    original_amount=float(amount),
                    birthday_discount=birthday_discount,
                    points_redeemed=points_to_redeem,
                    final_amount=final_after_redemption,
                    method=method,
                    ts=ts,
                )

                # 4) Record redemption (deduct from balance)
                if points_to_redeem > 0:
                    storage.record_redemption(
                        phone=st.session_state["phone"],
                        points=points_to_redeem,
                        ts=ts,
                    )

                # 5) Earn points from ORIGINAL (pre-discount) amount (expire in 1 year automatically via calc)
                earned = storage.calculate_points_for_amount(float(amount))

                # 6) Recompute balance (with expiry) and persist to customers.xlsx
                new_balance = storage.calculate_total_points(st.session_state["phone"], ts)
                storage.update_customer_points(st.session_state["phone"], new_balance)

                # 7) UI messages
                msg_main = (
                    f"You paid ${final_after_redemption:.2f} "
                    f"(original ${float(amount):.2f}"
                    f"{', $' + format(birthday_discount, '.2f') + ' birthday discount' if birthday_discount > 0 else ''}"
                    f"{', used ' + str(int(points_to_redeem)) + ' points' if points_to_redeem > 0 else ''}). "
                    f"Transaction saved and pushed to GitHub."
                )
                st.success(msg_main)

                st.info(
                    f"Loyalty: earned {earned:.2f} points on this purchase; "
                    f"updated balance (after expiry & redemptions): {new_balance:.2f} points."
                )

            except Exception as e:
                st.error(f"Failed to process payment: {e}")

# ---- Download customers.xlsx (admin use) ----
cust_bytes = storage.get_customers_file_bytes()
if cust_bytes:
    st.download_button(
        label="Download Customers Excel",
        data=cust_bytes,
        file_name="customers.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help="Download the latest customers.xlsx (includes total points per phone)"
    )
else:
    st.caption("No customers file found yet. Add a customer to create it.")

