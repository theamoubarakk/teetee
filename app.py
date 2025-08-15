from deps import st, re, datetime  # central imports
import storage                     # our storage helpers

# ---- session init ----
if "phone_valid" not in st.session_state:
    st.session_state["phone_valid"] = False
if "phone" not in st.session_state:
    st.session_state["phone"] = ""

st.title("💳 Payment Entry")

# ---- Step 1: phone capture ----
phone = st.text_input("Enter your phone number (exactly 8 digits):", value=st.session_state["phone"])

if st.button("Next"):
    if re.fullmatch(r"\d{8}", phone):
        st.session_state["phone_valid"] = True
        st.session_state["phone"] = phone
        st.success("Phone number accepted.")
    else:
        st.session_state["phone_valid"] = False
        st.error("Invalid phone number. Please enter exactly 8 digits (0–9).")

# ---- Step 2: payment if valid ----
if st.session_state["phone_valid"]:
    amount = st.number_input("Enter payment amount:", min_value=0.0, step=1.0, format="%.2f")
    note = st.text_input("Optional note (e.g., cash / reference)")

    if st.button("Submit Payment"):
        storage.save_payment(
            phone=st.session_state["phone"],
            amount=float(amount),
            note=note,
            ts=datetime.now().isoformat(timespec="seconds"),
        )
        st.success(f"Recorded ${amount:.2f} for {st.session_state['phone']}.")

# ---- Step 3: view data (optional) ----
with st.expander("Show all payments"):
    st.json(storage.load_payments())
