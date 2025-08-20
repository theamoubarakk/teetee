from deps import st, re, datetime
from datetime import date
import storage_github as storage

st.title("Loyalty Program")

# ---- helpers ----
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
st.session_state.setdefault("phone_valid", False)
st.session_state.setdefault("phone", "")
st.session_state.setdefault("profile_saved", False)
st.session_state.setdefault("edit_birthday", False)
# optimistic cache to beat GitHub read-after-write
st.session_state.setdefault("just_saved_phone", None)
st.session_state.setdefault("just_saved_birthday", None)

# =========================
# Step 1: phone capture (hidden once valid)
# =========================
if not st.session_state["phone_valid"]:
    phone = st.text_input("Enter your phone number (exactly 8 digits):", value=st.session_state["phone"])
    if st.button("Next"):
        if re.fullmatch(r"\d{8}", phone):
            st.session_state["phone_valid"] = True
            st.session_state["phone"] = phone
            st.rerun()
        else:
            st.session_state["phone_valid"] = False
            st.error("Invalid phone number. Please enter exactly 8 digits (0–9).")
else:
    # Compact header showing the locked-in phone and a way to change it
    col_p1, col_p2 = st.columns([1, 0.25])
    with col_p1:
        st.caption(f"Phone: **{st.session_state['phone']}**")
    with col_p2:
        if st.button("Change number"):
            # reset state so the phone input shows again
            st.session_state["phone_valid"] = False
            st.session_state["edit_birthday"] = False
            st.session_state["just_saved_phone"] = None
            st.session_state["just_saved_birthday"] = None
            st.rerun()

# =========================
# Step 1.5: profile (set or edit birthday)
# =========================
customer = None
if st.session_state["phone_valid"]:
    phone_id = st.session_state["phone"]

    # Load from GitHub
    try:
        customer = storage.get_customer(phone_id)
    except Exception as e:
        st.error(f"Failed to load customer profile: {e}")

    # Use optimistic cache if a save just happened
    cached_bday = st.session_state["just_saved_birthday"] if st.session_state["just_saved_phone"] == phone_id else None
    effective_bday_iso = cached_bday or (customer.get("birthday") if customer else None)

    if not effective_bday_iso:
        # ONLY show birthday input if none exists yet
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
                    # optimistic cache to cover GitHub propagation lag
                    st.session_state["just_saved_phone"] = phone_id
                    st.session_state["just_saved_birthday"] = dob.isoformat()
                    st.success("Profile saved.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to save profile: {e}")
    else:
        # Birthday exists → just show caption (no input box)
        st.caption(f"Profile → Birthday: {_fmt_birthday(effective_bday_iso)}")

        # Optional: allow editing via button
        col_a, _ = st.columns([1, 1])
        with col_a:
            if not st.session_state["edit_birthday"]:
                if st.button("Edit Birthday"):
                    st.session_state["edit_birthday"] = True

        if st.session_state["edit_birthday"]:
            default_date = _iso_to_date_or_none(effective_bday_iso) or date(2000, 1, 1)
            new_dob = st.date_input(
                "Set/Update Birthday",
                value=default_date,
                min_value=date(1960, 1, 1),
                max_value=date.today(),
                key="edit_bday"
            )
            c1, c2 = st.columns([1, 1])
            with c1:
                if st.button("Save Birthday"):
                    try:
                        storage.save_or_update_customer(phone=phone_id, birthday_iso=new_dob.isoformat())
                        st.session_state["just_saved_phone"] = phone_id
                        st.session_state["just_saved_birthday"] = new_dob.isoformat()
                        st.session_state["edit_birthday"] = False
                        st.success("Birthday saved.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to save birthday: {e}")
            with c2:
                if st.button("Cancel"):
                    st.session_state["edit_birthday"] = False

# =========================
# Step 2: payment (birthday discount + reward discount)
# =========================
if st.session_state["phone_valid"]:
    try:
        ts_now = datetime.now().isoformat(timespec="seconds")
        current_pts_for_ui = storage.calculate_total_points(st.session_state["phone"], ts_now)
        storage.update_customer_points(st.session_state["phone"], current_pts_for_ui)
        st.caption(f"Available points: {current_pts_for_ui:.2f}")
    except Exception:
        current_pts_for_ui = 0.0
        st.caption("Available points: (not available yet)")

    amount = st.number_input("Enter payment amount:", min_value=0.01, step=0.01, format="%.2f")
    method = st.selectbox("Payment Method", ["Cash", "Check", "Credit Card"])

    # Rewards picker (label without '(optional)')
    eligible = [(c, cash) for (c, cash) in storage.REWARD_TIERS if current_pts_for_ui >= c]
    reward_label_map = {"No reward": (0, 0.0)}
    for cost, cash in eligible:
        reward_label_map[f"{cost} pts → ${cash} off"] = (cost, float(cash))
    choice = st.selectbox("Apply reward discount", list(reward_label_map.keys()))
    sel_cost, sel_cash = reward_label_map[choice]

    if st.button("Submit Payment"):
        if amount <= 0:
            st.error("Amount must be greater than 0.")
        else:
            try:
                ts = datetime.now().isoformat(timespec="seconds")

                # 1) Birthday discount first
                after_bday, bday_discount = storage.apply_birthday_discount(
                    phone=st.session_state["phone"], amount=float(amount), ts=ts
                )

                # 2) Current points
                current_points = storage.calculate_total_points(st.session_state["phone"], ts)
                storage.update_customer_points(st.session_state["phone"], current_points)

                # 3) Reward discount (cash off) if selected and enough points
                reward_discount = 0.0
                points_spent_for_reward = 0.0
                if sel_cost > 0 and current_points >= sel_cost:
                    reward_discount = min(sel_cash, after_bday)
                    if reward_discount > 0:
                        points_spent_for_reward = float(sel_cost)
                        storage.record_redemption(st.session_state["phone"], points_spent_for_reward, ts)

                # 4) Final amount after discounts
                amount_due = max(0.0, round(after_bday - reward_discount, 2))

                # 5) Save payment
                storage.save_payment(
                    phone=st.session_state["phone"],
                    original_amount=float(amount),
                    birthday_discount=bday_discount,
                    reward_discount=reward_discount,
                    points_redeemed=points_spent_for_reward,
                    final_amount=amount_due,
                    method=method,
                    ts=ts,
                )

                # 6) Update points
                earned = storage.calculate_points_for_amount(float(amount))
                new_balance = max(0.0, current_points - points_spent_for_reward + earned)
                storage.update_customer_points(st.session_state["phone"], new_balance)

                # 7) UI feedback
                st.caption(
                    f"Birthday discount: {bday_discount:.2f} | "
                    f"Reward discount: {reward_discount:.2f} | "
                    f"Amount due: {amount_due:.2f}"
                )
                st.info(f"earned points: {earned:.2f}\n\ntotal points: {new_balance:.2f}")

            except Exception as e:
                st.error(f"Failed to process payment: {e}")

# ---- Download customers.xlsx ----
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

# ---- Admin: Clear all data ----
st.divider()
with st.expander("Admin • Clear all data"):
    st.warning("This will erase all rows in customers.xlsx, payments.xlsx, redemptions.xlsx (and vouchers.xlsx if present), keeping only headers.")
    confirm = st.checkbox("I understand this action is irreversible.", value=False)
    include_vouchers = st.checkbox("Also clear vouchers.xlsx (if present)", value=True)
    if st.button("Clear ALL data now", type="primary", disabled=not confirm):
        try:
            results = storage.clear_all_data(include_vouchers=include_vouchers)
            st.session_state["profile_saved"] = False
            st.session_state["edit_birthday"] = False
            st.session_state["just_saved_phone"] = None
            st.session_state["just_saved_birthday"] = None
            lines = [f"- {k}: {v}" for k, v in results.items()]
            st.success("Data cleared.\n\n" + "\n".join(lines))
        except Exception as e:
            st.error(f"Failed to clear data: {e}")
