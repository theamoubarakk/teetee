from deps import st, re, datetime
from datetime import date
import storage_github as storage

st.title("Payment Entry + Loyalty (Points + Reward Discount)")

# ---- session init ----
if "phone_valid" not in st.session_state:
    st.session_state["phone_valid"] = False
if "phone" not in st.session_state:
    st.session_state["phone"] = ""
if "profile_saved" not in st.session_state:
    st.session_state["profile_saved"] = False

# ---- Step 1: phone capture ----
phone = st.text_input(
    "Enter your phone number (exactly 8 digits):",
    value=st.session_state["phone"]
)
if st.button("Next"):
    if re.fullmatch(r"\d{8}", phone):
        st.session_state["phone_valid"] = True
        st.session_state["phone"] = phone
        # no success/green box
    else:
        st.session_state["phone_valid"] = False
        st.error("Invalid phone number. Please enter exactly 8 digits (0–9).")

# ---- Step 1.5: profile (birthday once) ----
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

# ---- Step 2: payment (birthday discount + optional reward discount) ----
if st.session_state["phone_valid"]:
    # Show available points for quick admin decision
    try:
        ts_now = datetime.now().isoformat(timespec="seconds")
        current_pts_for_ui = storage.calculate_total_points(st.session_state["phone"], ts_now)
        storage.update_customer_points(st.session_state["phone"], current_pts_for_ui)
        st.caption(f"Available points: {current_pts_for_ui:.2f}")
    except Exception:
        current_pts_for_ui = 0.0
        st.caption("Available points: (not available yet)")

    amount = st.number_input(
        "Enter payment amount:",
        min_value=0.01,
        step=0.01,
        format="%.2f"
    )
    method = st.selectbox("Payment Method", ["Cash", "Check", "Credit Card"])

    # Reward discount picker (admin-friendly, no vouchers)
    eligible = [(c, cash) for (c, cash) in storage.REWARD_TIERS if current_pts_for_ui >= c]
    reward_label_map = {"No reward": (0, 0.0)}
    for cost, cash in eligible:
        reward_label_map[f"{cost} pts → ${cash} off"] = (cost, float(cash))

    choice = st.selectbox("Apply reward discount (optional)", list(reward_label_map.keys()))
    sel_cost, sel_cash = reward_label_map[choice]

    if st.button("Submit Payment"):
        if amount <= 0:
            st.error("Amount must be greater than 0.")
        else:
            try:
                ts = datetime.now().isoformat(timespec="seconds")

                # 1) Birthday discount first (15% within 7 days before birthday)
                after_bday, bday_discount = storage.apply_birthday_discount(
                    phone=st.session_state["phone"],
                    amount=float(amount),
                    ts=ts,
                )

                # 2) Current points (with expiry)
                current_points = storage.calculate_total_points(st.session_state["phone"], ts)
                storage.update_customer_points(st.session_state["phone"], current_points)

                # 3) Reward discount (cash off) if selected and enough points
                reward_discount = 0.0
                points_spent_for_reward = 0.0
                if sel_cost > 0 and current_points >= sel_cost:
                    reward_discount = min(sel_cash, after_bday)  # cannot exceed amount due after birthday discount
                    if reward_discount > 0:
                        points_spent_for_reward = float(sel_cost)
                        # record points spent so balance math stays correct
                        storage.record_redemption(
                            phone=st.session_state["phone"],
                            points=points_spent_for_reward,
                            ts=ts,
                        )

                # 4) Final amount after discounts
                amount_due = max(0.0, round(after_bday - reward_discount, 2))

                # 5) Save payment (includes reward_discount column)
                storage.save_payment(
                    phone=st.session_state["phone"],
                    original_amount=float(amount),    # earn points on original amount
                    birthday_discount=bday_discount,
                    reward_discount=reward_discount,          # NEW COLUMN
                    points_redeemed=points_spent_for_reward,  # points spent to get reward
                    final_amount=amount_due,
                    method=method,
                    ts=ts,
                )

                # 6) Earn points from ORIGINAL amount
                earned = storage.calculate_points_for_amount(float(amount))

                # 7) New balance locally (avoid read-after-write lag)
                new_balance = max(0.0, current_points - points_spent_for_reward + earned)
                storage.update_customer_points(st.session_state["phone"], new_balance)

                # 8) Minimal blue box (exactly two lines)
                st.info(f"earned points: {earned:.2f}\n\ntotal points: {new_balance:.2f}")

            except Exception as e:
                st.error(f"Failed to process payment: {e}")

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
