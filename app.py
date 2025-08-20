from deps import st, re, datetime
from datetime import date
import storage_github as storage

st.title("Payment Entry + Loyalty (Points + Reward Discount)")

# ---- tiny helper to format birthday safely (never shows 'nan') ----
def _fmt_birthday(value):
    if not value:
        return "—"
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d %b %Y")
    except Exception:
        return "—"

def _iso_to_date_or_none(val):
    if not val:
        return None
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except Exception:
        return None

# ---- session init ----
if "phone_valid" not in st.session_state:
    st.session_state["phone_valid"] = False
if "phone" not in st.session_state:
    st.session_state["phone"] = ""
if "profile_saved" not in st.session_state:
    st.session_state["profile_saved"] = False
if "edit_birthday" not in st.session_state:
    st.session_state["edit_birthday"] = False

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

# ---- Step 1.5: profile (set or edit birthday) ----
customer = None
if st.session_state["phone_valid"]:
    try:
        customer = storage.get_customer(st.session_state["phone"])
    except Exception as e:
        st.error(f"Failed to load customer profile: {e}")

    phone_id = st.session_state["phone"]

    if customer is None:
        # New customer → must set birthday once
        st.info("New customer detected. Please add a birthday.")
        dob = st.date_input(
            "Birthday (required)",
            min_value=date(1960, 1, 1),
            max_value=date.today(),
            key="new_bday"
        )
        if st.button("Save Profile"):
            if dob is None:
                st.error("Birthday is required.")
            else:
                try:
                    storage.save_or_update_customer(phone=phone_id, birthday_iso=dob.isoformat())
                    storage.update_customer_points(phone_id, 0.0)
                    st.session_state["profile_saved"] = True
                    st.success("Profile saved.")
                except Exception as e:
                    st.error(f"Failed to save profile: {e}")
    else:
        # Existing customer
        current_bday_iso = customer.get("birthday")
        st.caption(f"Profile → Birthday: {_fmt_birthday(current_bday_iso)}")

        # If birthday missing, prompt to set it; if present, allow edit
        if not current_bday_iso and not st.session_state["edit_birthday"]:
            st.warning("No birthday saved yet for this customer.")
            st.session_state["edit_birthday"] = True

        col_a, col_b = st.columns([1,1])
        with col_a:
            if not st.session_state["edit_birthday"]:
                if st.button("Edit Birthday"):
                    st.session_state["edit_birthday"] = True

        if st.session_state["edit_birthday"]:
            default_date = _iso_to_date_or_none(current_bday_iso) or date(2000, 1, 1)
            new_dob = st.date_input(
                "Set/Update Birthday",
                value=default_date,
                min_value=date(1960, 1, 1),
                max_value=date.today(),
                key="edit_bday"
            )
            c1, c2 = st.columns([1,1])
            with c1:
                if st.button("Save Birthday"):
                    try:
                        storage.save_or_update_customer(phone=phone_id, birthday_iso=new_dob.isoformat())
                        st.session_state["edit_birthday"] = False
                        st.success("Birthday saved.")
                    except Exception as e:
                        st.error(f"Failed to save birthday: {e}")
            with c2:
                if st.button("Cancel"):
                    st.session_state["edit_birthday"] = False

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
