import streamlit as st
import pandas as pd
import sqlite3
import fitz  # PyMuPDF
import re
from fpdf import FPDF

# --- Database Management ---
DB_FILE = "debts.db"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        # Create Cards Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                balance REAL NOT NULL,
                apr REAL NOT NULL,
                min_pay REAL NOT NULL,
                custom_pay REAL DEFAULT 0.0
            )
        """)
        
        # Create Global Settings Table to remember budget & currency
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        
        try:
            conn.execute("ALTER TABLE cards ADD COLUMN custom_pay REAL DEFAULT 0.0")
        except sqlite3.OperationalError:
            pass 
            
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cards")
        if cursor.fetchone()[0] == 0:
            initial_cards = [
                ('Store Card', 1200.0, 25.99, 35.0, 100.0),
                ('Travel Card', 4500.0, 19.99, 120.0, 150.0)
            ]
            conn.executemany("INSERT INTO cards (name, balance, apr, min_pay, custom_pay) VALUES (?, ?, ?, ?, ?)", initial_cards)
            conn.commit()

# --- Settings Helper Functions ---
def load_setting(key, default_value):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cursor.fetchone()
        return row[0] if row else default_value

def save_setting(key, value):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def load_cards():
    with sqlite3.connect(DB_FILE) as conn:
        return pd.read_sql_query("SELECT id, name, balance, apr, min_pay, custom_pay FROM cards", conn)

def save_cards(df):
    def safe_float(val):
        if pd.isna(val) or val is None or str(val).strip() == "": return 0.0
        try: return float(val)
        except ValueError: return 0.0

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM cards")
        for _, row in df.iterrows():
            if pd.isna(row.get('name')) or str(row.get('name')).strip() == "":
                continue
                
            conn.execute(
                "INSERT INTO cards (name, balance, apr, min_pay, custom_pay) VALUES (?, ?, ?, ?, ?)",
                (str(row['name']), safe_float(row.get('balance')), safe_float(row.get('apr')), safe_float(row.get('min_pay')), safe_float(row.get('custom_pay')))
            )
        conn.commit()

def process_monthly_rollover(cards_list, action_plan):
    updated_cards = []
    for c in cards_list:
        if c['balance'] <= 0:
            updated_cards.append(c)
            continue
            
        base_interest = c['balance'] * ((c['apr'] / 100) / 12)
        gst_charge = base_interest * 0.18 # 18% GST on Interest
        total_monthly_fee = base_interest + gst_charge
        
        payment_made = action_plan.get(c['name'], 0.0)
        
        new_balance = c['balance'] + total_monthly_fee - payment_made
        c['balance'] = max(0.0, round(new_balance, 2)) 
        updated_cards.append(c)
        
    save_cards(pd.DataFrame(updated_cards))

# --- PDF Generation Helper ---
def generate_pdf_report(df, action_plan, currency_name, total_months, total_interest, total_gst):
    pdf = FPDF()
    pdf.add_page()
    
    def safe_text(text):
        return str(text).encode('latin-1', 'replace').decode('latin-1')
    
    safe_currency = currency_name.split(" ")[0]
    
    # Title
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, safe_text("Debt Payoff Action Plan & Projection"), ln=True, align='C')
    pdf.ln(5)
    
    # Summary
    pdf.set_font("Arial", size=10)
    pdf.cell(0, 10, safe_text(f"Currency Used: {safe_currency}"), ln=True)
    pdf.cell(0, 10, safe_text(f"Total Time to Freedom: {total_months} Months"), ln=True)
    pdf.cell(0, 10, safe_text(f"Total Bank Interest: {total_interest:,.2f}"), ln=True)
    pdf.cell(0, 10, safe_text(f"Total GST (18%): {total_gst:,.2f}"), ln=True)
    pdf.cell(0, 10, safe_text(f"Total Wasteful Cost (Int + GST): {(total_interest + total_gst):,.2f}"), ln=True)
    pdf.ln(5)
    
    # Action Plan
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, safe_text("Action Plan for This Month:"), ln=True)
    pdf.set_font("Arial", size=12)
    for card, amount in action_plan.items():
        pdf.cell(0, 10, safe_text(f"- {card}: {amount:,.2f}"), ln=True)
        
    # Table Header
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, safe_text("Amortization Schedule:"), ln=True)
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(20, 10, safe_text("Month"), border=1)
    pdf.cell(45, 10, safe_text("Balance"), border=1)
    pdf.cell(35, 10, safe_text("Interest Paid"), border=1)
    pdf.cell(35, 10, safe_text("GST (18%)"), border=1)
    pdf.cell(45, 10, safe_text("Total Lost to Fees"), border=1)
    pdf.ln()
    
    # Table Body
    pdf.set_font("Arial", size=9)
    for _, row in df.iterrows():
        total_fees = row['Interest Paid'] + row['GST Paid']
        pdf.cell(20, 10, safe_text(str(row['Month'])), border=1)
        pdf.cell(45, 10, safe_text(f"{row['Remaining Balance']:,.2f}"), border=1)
        pdf.cell(35, 10, safe_text(f"{row['Interest Paid']:,.2f}"), border=1)
        pdf.cell(35, 10, safe_text(f"{row['GST Paid']:,.2f}"), border=1)
        pdf.cell(45, 10, safe_text(f"{total_fees:,.2f}"), border=1)
        pdf.ln()
        
    return pdf.output(dest='S').encode('latin-1')

# --- Payoff Simulation Logic ---
def calculate_multi_card_payoff(accounts, total_monthly_budget, strategy):
    debts = [dict(acc) for acc in accounts]
    schedule = []
    month = 1
    action_plan = {d['name']: 0.0 for d in debts}
    
    while True:
        total_balance = sum(d['balance'] for d in debts if d['balance'] > 0)
        if total_balance <= 0 or month > 360: break
            
        monthly_interest_total = 0
        monthly_gst_total = 0
        
        for debt in debts:
            if debt['balance'] > 0:
                base_interest = debt['balance'] * ((debt['apr'] / 100) / 12)
                gst = base_interest * 0.18 # 18% Tax
                total_charge = base_interest + gst
                
                debt['balance'] += total_charge
                monthly_interest_total += base_interest
                monthly_gst_total += gst

        if strategy == "Custom Allocation (Use My Numbers)":
            for debt in debts:
                if debt['balance'] > 0:
                    payment = min(debt['custom_pay'], debt['balance'])
                    debt['balance'] -= payment
                    if month == 1: action_plan[debt['name']] += payment
        else:
            if strategy == "Avalanche (Highest APR)": debts.sort(key=lambda x: x['apr'], reverse=True)
            else: debts.sort(key=lambda x: x['balance'])

            remaining_budget = total_monthly_budget
            
            for debt in debts:
                if debt['balance'] > 0:
                    payment = min(debt['min_pay'], debt['balance'])
                    debt['balance'] -= payment
                    remaining_budget -= payment
                    if month == 1: action_plan[debt['name']] += payment
                    
            for debt in debts:
                if remaining_budget <= 0: break
                if debt['balance'] > 0:
                    extra = min(remaining_budget, debt['balance'])
                    debt['balance'] -= extra
                    remaining_budget -= extra
                    if month == 1: action_plan[debt['name']] += extra
                
        schedule.append({
            "Month": month,
            "Remaining Balance": round(sum(d['balance'] for d in debts if d['balance'] > 0), 2),
            "Interest Paid": round(monthly_interest_total, 2),
            "GST Paid": round(monthly_gst_total, 2)
        })
        month += 1
        
    return pd.DataFrame(schedule), action_plan

# --- Streamlit Presentation Layer ---
st.set_page_config(page_title="Debt Payoff Webapp", layout="wide", page_icon="💳")
init_db()

st.title("💳 Ultimate Credit Card Payment Planner")

# --- MEMORY RETRIEVAL FOR SIDEBAR ---
saved_currency = load_setting("currency", "INR (₹)")
saved_strategy = load_setting("strategy", "Avalanche (Highest APR)")
saved_budget = float(load_setting("budget", 500.0))

# Sidebar
st.sidebar.header("⚙️ Global Configurations")
currency_options = {"INR (₹)": "₹", "USD ($)": "$", "EUR (€)": "€", "GBP (£)": "£"}

# Set the dropdown index based on what was saved in the database
currency_index = list(currency_options.keys()).index(saved_currency) if saved_currency in currency_options else 0
selected_currency = st.sidebar.selectbox("Preferred Currency", list(currency_options.keys()), index=currency_index)
currency_symbol = currency_options[selected_currency]

strategy_options = ["Avalanche (Highest APR)", "Snowball (Lowest Balance)", "Custom Allocation (Use My Numbers)"]
strategy_index = strategy_options.index(saved_strategy) if saved_strategy in strategy_options else 0
strategy = st.sidebar.radio("Payoff Strategy", strategy_options, index=strategy_index)

if strategy != "Custom Allocation (Use My Numbers)":
    budget = st.sidebar.number_input(f"Total Monthly Budget ({currency_symbol})", min_value=10.0, value=saved_budget, step=50.0)
else:
    budget = 0.0

# --- INSTANT SAVE TO MEMORY ---
# If you change any of these settings, write them to the database immediately so they never reset
if selected_currency != saved_currency: save_setting("currency", selected_currency)
if strategy != saved_strategy: save_setting("strategy", strategy)
if budget != saved_budget and strategy != "Custom Allocation (Use My Numbers)": save_setting("budget", budget)

# --- USABILITY TABS ---
tab_ledger, tab_reports = st.tabs(["📝 Step 1: Data Entry & Ledger", "📊 Step 2: Action Plan & Reports"])

with tab_ledger:
    with st.expander("📁 Scan a Bill Invoice (Optional)"):
        uploaded_file = st.file_uploader("Upload a statement PDF to extract details", type=["pdf"])
        if uploaded_file is not None:
            try:
                doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
                is_unlocked = not doc.needs_pass
                if not is_unlocked:
                    pdf_password = st.text_input("🔒 Enter PDF password:", type="password")
                    if pdf_password and doc.authenticate(pdf_password): is_unlocked = True
                    
                if is_unlocked:
                    pdf_text = "".join([page.get_text() for page in doc])
                    bal_match = re.search(r'(?:New Balance|Total Amount Due|Current Balance)[:\s]*[$\u20B9\u20AC\u00A3]?\s*([0-9,]+\.\d{2})', pdf_text, re.IGNORECASE)
                    min_match = re.search(r'(?:Minimum Payment|Minimum Due)[:\s]*[$\u20B9\u20AC\u00A3]?\s*([0-9,]+\.\d{2})', pdf_text, re.IGNORECASE)
                    
                    if bal_match and min_match:
                        st.success(f"🎉 Found! Balance: **{currency_symbol}{float(bal_match.group(1).replace(',', '')):,.2f}** | Min Due: **{currency_symbol}{float(min_match.group(1).replace(',', '')):,.2f}**")
            except Exception as e:
                st.error(f"Error: {e}")

    st.subheader("Update Your Daily Card Ledger")
    
    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("🚨 Emergency Reset", help="Deletes all data and resets the table to defaults"):
            with sqlite3.connect(DB_FILE) as conn: 
                conn.execute("DROP TABLE IF EXISTS cards")
                conn.execute("DROP TABLE IF EXISTS settings") # Clear settings too on hard reset
            init_db()
            st.rerun()

    df_current = load_cards()
    edited_df = st.data_editor(
        df_current, 
        column_config={
            "id": None, 
            "name": st.column_config.TextColumn("Card/Bank Name", required=True),
            "balance": st.column_config.NumberColumn(f"Balance ({currency_symbol})", format=f"{currency_symbol} %.2f", min_value=0.0),
            "apr": st.column_config.NumberColumn("APR (%)", format="%.2f %%", min_value=0.0),
            "min_pay": st.column_config.NumberColumn(f"Min Due ({currency_symbol})", format=f"{currency_symbol} %.2f", min_value=0.0),
            "custom_pay": st.column_config.NumberColumn(f"Target Pay ({currency_symbol})", format=f"{currency_symbol} %.2f", min_value=0.0)
        },
        num_rows="dynamic",
        use_container_width=True
    )

    current_total_balance = pd.to_numeric(edited_df['balance'], errors='coerce').fillna(0).sum()
    current_total_min = pd.to_numeric(edited_df['min_pay'], errors='coerce').fillna(0).sum()
    
    st.markdown("### Ledger Totals")
    tot1, tot2, _ = st.columns(3)
    tot1.metric("Total Combined Debt", f"{currency_symbol}{current_total_balance:,.2f}")
    tot2.metric("Total Minimums Due", f"{currency_symbol}{current_total_min:,.2f}")

    if st.button("💾 Save Database Changes", type="primary", use_container_width=True):
        save_cards(edited_df)
        st.success("Ledger Saved! Move to the 'Reports' tab to see your projections.")

with tab_reports:
    clean_df = load_cards()
    cards_list = clean_df.to_dict('records')
    total_mins = sum(c['min_pay'] for c in cards_list)
    
    if len(cards_list) == 0 or sum(c['balance'] for c in cards_list) <= 0:
        st.success("🎉 You have no active balances! Your debt is 0.")
    elif strategy != "Custom Allocation (Use My Numbers)" and total_mins > budget:
        st.error(f"Error: Your combined minimum payments ({currency_symbol}{total_mins:,.2f}) exceed your set Monthly Budget ({currency_symbol}{budget}). Go to the sidebar and increase your budget.")
    else:
        df_results, action_plan = calculate_multi_card_payoff(cards_list, budget, strategy)
        
        if not df_results.empty:
            st.header("🎯 Your Action Plan for This Month")
            st.write("Pay exactly these amounts today to stay on track with your strategy:")
            
            cols = st.columns(len(action_plan))
            for i, (card_name, pay_amount) in enumerate(action_plan.items()):
                with cols[i]:
                    st.success(f"**{card_name}**\n\n### {currency_symbol}{pay_amount:,.2f}")
            
            st.markdown("<br>", unsafe_allow_html=True)
            with st.expander("✅ Done paying these? Click here to advance to next month."):
                st.write("Clicking this button will simulate 1 month passing: It adds interest + 18% GST to your cards and subtracts the exact payments you made above.")
                if st.button("🔄 Mark Payments as Completed (Rollover)", type="primary"):
                    process_monthly_rollover(cards_list, action_plan)
                    st.balloons()
                    st.success("Balances Updated! Refreshing the app...")
                    st.rerun()
                    
            st.markdown("---")
            
            st.subheader("📈 Long-Term Cost Projections")
            total_interest_paid = df_results['Interest Paid'].sum()
            total_gst_paid = df_results['GST Paid'].sum()
            total_fees = total_interest_paid + total_gst_paid
            
            m1, m2, m3 = st.columns(3)
            m1.metric("Timeline to Freedom", f"{len(df_results)} Months")
            m2.metric("Total Bank Interest", f"{currency_symbol}{total_interest_paid:,.2f}")
            m3.metric("Total Gov. GST (18%)", f"{currency_symbol}{total_gst_paid:,.2f}", delta=f"Total Cost: {currency_symbol}{total_fees:,.2f}", delta_color="inverse")
            
            st.line_chart(df_results.set_index("Month")["Remaining Balance"])
            
            st.subheader("🗓️ Month-by-Month Breakdown")
            st.dataframe(df_results, use_container_width=True)
            
            # --- FILE EXPORT SECTION ---
            st.markdown("---")
            st.subheader("📥 Export Your Data")
            st.write("Click the button below to generate your files safely.")
            
            if 'prep_downloads' not in st.session_state:
                st.session_state.prep_downloads = False

            if st.button("🔄 Generate Download Files"):
                st.session_state.prep_downloads = True

            if st.session_state.prep_downloads:
                dl_col1, dl_col2 = st.columns(2)
                
                with dl_col1:
                    csv_data = df_results.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📊 Download CSV (Excel)",
                        data=csv_data,
                        file_name="debt_projection.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                    
                with dl_col2:
                    pdf_data = generate_pdf_report(
                        df=df_results, 
                        action_plan=action_plan, 
                        currency_name=selected_currency,
                        total_months=len(df_results),
                        total_interest=total_interest_paid,
                        total_gst=total_gst_paid
                    )
                    
                    st.download_button(
                        label="📄 Download PDF Report",
                        data=pdf_data,
                        file_name="debt_action_plan.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )
