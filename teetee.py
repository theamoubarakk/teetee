# Path to store payment records
PAYMENT_FILE = "payments.json"

# Load existing payment data
def load_payments():
    if os.path.exists(PAYMENT_FILE):
        with open(PAYMENT_FILE, "r") as f:
            return json.load(f)
    return {}

# Save payment data
def save_payments(data):
    with open(PAYMENT_FILE, "w") as f:
        json.dump(data, f, indent=4)

# App title
st.title("💳 Payment Entry")

# Step 1: Get phone number
phone = st.text_input("Enter your phone number (8 digits):")

# Validate phone number
if st.button("Next"):
    if re.fullmatch(r"\d{8}", phone):
        st.session_state["phone_valid"] = True
        st.session_state["phone"] = phone
    else:
        st.error("Invalid phone number. Please enter exactly 8 digits.")

# Step 2: If phone is valid, ask for payment amount
if "phone_valid" in st.session_state and st.session_state["phone_valid"]:
    amount = st.number_input("Enter payment amount:", min_value=0.0, format="%.2f")
    if st.button("Submit Payment"):
        payments = load_payments()
        payments[st.session_state["phone"]] = amount
        save_payments(payments)
        st.success(f"Payment of ${amount:.2f} recorded for {st.session_state['phone']}")

# Display existing payments
if st.checkbox("Show all payments"):
    payments = load_payments()
    if payments:
        st.write(payments)
    else:
        st.info("No payments recorded yet.")
