import streamlit as st
import pandas as pd
from datetime import datetime
from scipy.optimize import newton
import io

# --- Original Helper Functions (from portfolio_irr.py) ---

def parse_date(date_str):
    """Parse date from string in multiple formats"""
    if pd.isna(date_str) or str(date_str).strip() == '':
        return None
    
    try:
        return pd.to_datetime(str(date_str), format="%m/%d/%Y")
    except ValueError:
        try:
            return pd.to_datetime(str(date_str), format="%d-%m-%Y")
        except ValueError:
            return None

def is_option_symbol(symbol):
    """Check if symbol is an option (contains expiry date and C/P)"""
    if pd.isna(symbol):
        return False
    s = str(symbol).strip()
    return len(s) > 10 and any(c in s for c in ['C', 'P']) and any(d.isdigit() for d in s)

def xnpv(rate, cashflows):
    """Calculate Net Present Value for irregular cash flows"""
    if not cashflows:
        return 0
    
    d0 = cashflows[0][0]
    return sum(cf / ((1 + rate) ** ((d - d0).days / 365.0)) for d, cf in cashflows if cf != 0)

def calculate_xirr(cashflows):
    """Calculate XIRR using Newton's method with multiple starting points"""
    if len(cashflows) < 2:
        return None
    
    # Filter out zero cashflows
    cashflows = [(d, cf) for d, cf in cashflows if abs(cf) > 0.01]
    
    if len(cashflows) < 2:
        return None
    
    # Check if all cashflows have same sign (XIRR undefined)
    signs = [1 if cf > 0 else -1 for _, cf in cashflows]
    if len(set(signs)) == 1:
        return None
    
    # Try multiple starting points for Newton's method
    starting_rates = [0.1, 0.01, 0.5, -0.5, 0.2, -0.2, 1.0, -0.9] # Original starting rates
    
    for start_rate in starting_rates:
        try:
            result = newton(lambda r: xnpv(r, cashflows), start_rate, maxiter=1000, tol=1e-6) # Original tol
            # Validate result is reasonable (between -99% and 10000%)
            if -0.99 <= result <= 100:
                return result
        except: # Original broad exception handling
            continue
    
    return None

def calculate_stock_only_xirr(ticker, df_trans, prices_dict, calculation_date):
    """Calculate XIRR for stock transactions only (excluding options)"""
    st.subheader(f"📈 Stock-Only XIRR for {ticker}")
    st.markdown("---") # Added for visual separation
    
    # Filter for stock transactions only (not options)
    stock_trans = df_trans[
        (df_trans['Symbol'] == ticker) & 
        (~df_trans['Symbol'].apply(is_option_symbol))
    ].copy()
    
    if stock_trans.empty:
        st.info("No stock transactions found.")
        return None
    
    # Sort by date
    stock_trans = stock_trans.sort_values('Date')
    
    cashflows = []
    total_qty = 0
    
    st.markdown("**Stock Transactions:**")
    for _, row in stock_trans.iterrows():
        date = row['Date']
        qty = row['Qty']
        cash_flow = row['Cash Flow']
        
        cashflows.append((date, cash_flow))
        total_qty += qty
        
        action = "BUY" if cash_flow < 0 else "SELL"
        st.write(f"  {date.date()}: {action} {qty:>8.3f} shares for ${cash_flow:>10.2f}")
    
    st.write(f"\nNet Stock Position: {total_qty:.3f} shares")
    
    # Add terminal value for current holdings
    current_price = prices_dict.get(ticker)
    if current_price and total_qty != 0:
        terminal_value = total_qty * current_price
        cashflows.append((calculation_date, terminal_value)) # Use calculation_date for consistency
        st.write(f"Current Holdings Value: {total_qty:.3f} × ${current_price} = ${terminal_value:,.2f}")
    
    # Calculate XIRR
    if len(cashflows) >= 2:
        st.markdown("**Stock Cash Flow Summary:**")
        # Display cash flows in a DataFrame for better readability
        df_cashflows = pd.DataFrame(sorted(cashflows, key=lambda x: x[0]), columns=['Date', 'Cash Flow'])
        st.dataframe(df_cashflows.style.format({'Cash Flow': '${:,.2f}'}))
        
        with st.spinner("Calculating Stock-Only XIRR..."):
            xirr = calculate_xirr(sorted(cashflows, key=lambda x: x[0]))
        if xirr is not None:
            st.success(f"✅ Stock-Only XIRR: {xirr:.3%}")
            return xirr
        else:
            st.warning("⚠️ Could not calculate XIRR for stock-only transactions.")
    else:
        st.warning("⚠️ Not enough cash flows for stock-only XIRR calculation.")
    
    return None

def calculate_combined_xirr(ticker, df_trans, prices_dict, calculation_date):
    """Calculate XIRR including both stock and option transactions"""
    st.subheader(f"📊 Combined XIRR for {ticker} (Stock + Options)")
    st.markdown("---") # Added for visual separation
    
    # Get all transactions for this ticker (stock and options)
    ticker_trans = df_trans[
        df_trans['Symbol'].str.startswith(ticker)
    ].copy().sort_values('Date')
    
    if ticker_trans.empty:
        st.info("No transactions found.")
        return None
    
    cashflows = []
    stock_qty = 0
    option_positions = {}
    
    st.markdown("**All Transactions (Stock & Options):**")
    
    for _, row in ticker_trans.iterrows():
        symbol = row['Symbol']
        date = row['Date']
        qty = row['Qty']
        cash_flow = row['Cash Flow']
        
        cashflows.append((date, cash_flow))
        
        if is_option_symbol(symbol):
            # Option transaction
            if symbol not in option_positions:
                option_positions[symbol] = {'net_qty': 0, 'transactions': []}
            
            option_positions[symbol]['net_qty'] += qty
            option_positions[symbol]['transactions'].append(row)
            
            action = "SELL" if qty < 0 else "BUY"
            st.write(f"  {date.date()}: OPTION {action} {abs(qty):>6.0f} contracts {symbol} for ${cash_flow:>10.2f}")
        else:
            # Stock transaction
            stock_qty += qty
            action = "BUY" if cash_flow < 0 else "SELL"
            st.write(f"  {date.date()}: STOCK  {action} {qty:>8.3f} shares {symbol} for ${cash_flow:>10.2f}")
    
    st.write(f"\nNet Stock Position: {stock_qty:.3f} shares")
    
    # Handle current option values
    if option_positions:
        st.markdown("**Option Positions:**")
        for symbol, position in option_positions.items():
            net_qty = position['net_qty']
            st.write(f"  {symbol}: {net_qty:>6.0f} contracts")
            
            if net_qty != 0:
                try:
                    # Parse expiry date - REVERTED TO ORIGINAL SLICING
                    ticker_len = len(ticker)
                    date_part = symbol[ticker_len:ticker_len+6]  # YYMMDD
                    expiry = datetime.strptime(date_part, "%y%m%d")
                    
                    st.write(f"    Expires: {expiry.date()}")
                    
                    if expiry >= datetime.today(): # ORIGINAL LOGIC: compare with datetime.today()
                        current_option_price = prices_dict.get(symbol)
                        if current_option_price:
                            option_value = current_option_price * net_qty * 100
                            cashflows.append((calculation_date, option_value)) # Use calculation_date
                            st.write(f"    Current value: ${current_option_price} × {net_qty} × 100 = ${option_value:,.2f}")
                        else:
                            st.warning(f"    No current price available for option {symbol}.")
                    else:
                        st.info(f"    Option {symbol} expired (assumed worthless).") # Changed print to st.info
                        
                except Exception as e: # Original broad exception handling
                    st.error(f"    Error parsing expiry for {symbol}: {e}") # Changed print to st.error
    
    # Add terminal value for stock holdings
    current_stock_price = prices_dict.get(ticker)
    if current_stock_price and stock_qty != 0:
        terminal_value = stock_qty * current_stock_price
        cashflows.append((calculation_date, terminal_value)) # Use calculation_date
        st.write(f"\nCurrent Stock Value: {stock_qty:.3f} × ${current_stock_price} = ${terminal_value:,.2f}")
    
    # Calculate XIRR
    if len(cashflows) >= 2:
        st.markdown("**Combined Cash Flow Summary:**")
        # Display cash flows in a DataFrame for better readability
        df_cashflows_combined = pd.DataFrame(sorted(cashflows, key=lambda x: x[0]), columns=['Date', 'Cash Flow'])
        st.dataframe(df_cashflows_combined.style.format({'Cash Flow': '${:,.2f}'}))

        with st.spinner("Calculating Combined XIRR..."):
            xirr = calculate_xirr(sorted(cashflows, key=lambda x: x[0]))
        if xirr is not None:
            st.success(f"✅ Combined XIRR: {xirr:.3%}")
            return xirr
        else:
            st.warning("⚠️ Could not calculate XIRR for combined transactions.")
    else:
        st.warning("⚠️ Not enough cash flows for combined XIRR calculation.")
    
    return None

# --- Streamlit App Layout ---

st.set_page_config(layout="centered", page_title="Portfolio XIRR Calculator")

st.title("🚀 Portfolio XIRR Calculator")

# Info button
with st.expander("ℹ️ How to Use"):
    st.markdown("""
    Paste your transaction data and current price data into the respective text areas below.
    
    **Transaction Data Format (CSV-like, with header row):**
    
    ```
    Symbol,Date,Qty,Cash Flow
    BKE,3/18/2025,25,-939.75
    BKE,4/30/2025,-30,1032.57
    BKE251219C00040000,6/23/2025,-1,649
    BOOT,2/6/2025,3,-435.96
    ```
    - `Symbol`: Stock ticker or option symbol (e.g., BKE, BKE251219C00040000)
    - `Date`: Transaction date (MM/DD/YYYY or DD-MM-YYYY)
    - `Qty`: Quantity of shares/contracts (positive for buy, negative for sell)
    - `Cash Flow`: Cash amount (negative for money out, positive for money in)
    
    **Current Price Data Format (CSV-like, with header row):**
    
    ```
    Symbol,Current Price
    BKE,49.7
    BOOT,170
    BKE251219C00040000,11.2
    ```
    - `Symbol`: Stock ticker or option symbol
    - `Current Price`: The current price of the stock/option
    
    After pasting, enter the main stock ticker you want to analyze (e.g., `BKE` or `BOOT`) and click "Calculate XIRR".
    """)

st.markdown("---")

# Initialize session state for inputs if not already present
if 'transactions_input' not in st.session_state:
    st.session_state.transactions_input = "Symbol,Date,Qty,Cash Flow\nBKE,3/18/2025,25,-939.75\nBKE,3/24/2025,25,-985.5\nBKE,3/28/2025,25,-942.8\nBKE,4/2/2025,25,-950.75\nBKE,4/4/2025,30,-1031.7\nBKE,4/29/2025,1.315,-45.5\nBKE,4/30/2025,-30,1032.57\nBKE250417C00040000,4/2/2025,-1,51\nBKE250417C00040000,4/17/2025,1,0\nBKE250620C00040000,4/23/2025,-1,89\nBKE250620C00040000,6/20/2025,1,-521\nBKE251219C00040000,6/23/2025,-1,649\nBOOT,2/6/2025,3,-435.96\nBOOT,2/13/2025,4,-581.28\nBOOT,2/19/2025,3,-436.5\nBOOT,2/26/2025,3,-437.55\nBOOT,3/4/2025,3,-438.27\nBOOT,3/11/2025,3,-439.86\nBOOT,3/18/2025,3,-440.13\nBOOT,3/25/2025,3,-440.73\nBOOT,4/1/2025,3,-441.36\nBOOT,4/8/2025,3,-442.23\nBOOT,4/15/2025,3,-443.1\nBOOT,4/22/2025,3,-443.97\nBOOT,4/29/2025,3,-444.84\nBOOT,5/6/2025,3,-445.71\nBOOT,5/13/2025,3,-446.58\nBOOT,5/20/2025,3,-447.45\nBOOT,5/27/2025,3,-448.32\nBOOT,6/3/2025,3,-449.19\nBOOT,6/10/2025,3,-450.06\nBOOT,6/17/2025,3,-450.93\nBOOT,6/24/2025,3,-451.8\nBOOT,7/1/2025,3,-452.67\nBOOT,7/8/2025,3,-453.54\nBOOT,7/15/2025,3,-454.41\nBOOT,7/22/2025,3,-455.28\nBOOT,7/29/2025,3,-456.15\nBOOT,8/5/2025,3,-457.02\nBOOT,8/12/2025,3,-457.89\nBOOT,8/19/2025,3,-458.76\nBOOT,8/26/2025,3,-459.63\nBOOT,9/2/2025,3,-460.5\nBOOT,9/9/2025,3,-461.37\nBOOT,9/16/2025,3,-462.24\nBOOT,9/23/2025,3,-463.11\nBOOT,9/30/2025,3,-463.98\nBOOT,10/7/2025,3,-464.85\nBOOT,10/14/2025,3,-465.72\nBOOT,10/21/2025,3,-466.59\nBOOT,10/28/2025,3,-467.46\nBOOT,11/4/2025,3,-468.33\nBOOT,11/11/2025,3,-469.2\nBOOT,11/18/2025,3,-470.07\nBOOT,11/25/2025,3,-470.94\nBOOT,12/2/2025,3,-471.81\nBOOT,12/9/2025,3,-472.68\nBOOT,12/16/2025,3,-473.55\nBOOT,12/23/2025,3,-474.42\nBOOT,12/30/2025,3,-475.29\n"
if 'prices_input' not in st.session_state:
    st.session_state.prices_input = "Symbol,Current Price\nBKE,49.7\nBOOT,170\nBKE251219C00040000,11.2\n"
if 'ticker_symbol' not in st.session_state:
    st.session_state.ticker_symbol = "BKE"

col1, col2 = st.columns(2)

with col1:
    transactions_input = st.text_area(
        "Paste your **Transaction Data** here (CSV format)",
        height=300,
        value=st.session_state.transactions_input,
        key="transactions_textarea"
    )

with col2:
    prices_input = st.text_area(
        "Paste your **Current Price Data** here (CSV format)",
        height=300,
        value=st.session_state.prices_input,
        key="prices_textarea"
    )

ticker_symbol = st.text_input(
    "Enter the **Ticker Symbol** to analyze (e.g., BKE, BOOT)",
    value=st.session_state.ticker_symbol,
    key="ticker_input"
).upper()

# Buttons for actions
col_buttons = st.columns(2)
with col_buttons[0]:
    calculate_button = st.button("Calculate XIRR", type="primary")
with col_buttons[1]:
    if st.button("Reset All"):
        st.session_state.transactions_input = "Symbol,Date,Qty,Cash Flow\n"
        st.session_state.prices_input = "Symbol,Current Price\n"
        st.session_state.ticker_symbol = ""
        st.experimental_rerun() # Rerun to clear inputs

if calculate_button:
    if not transactions_input.strip() or not prices_input.strip() or not ticker_symbol.strip():
        st.error("Please paste both transaction and current price data, and enter a ticker symbol.")
    else:
        with st.spinner("Loading and parsing data..."):
            try:
                # Load transaction data from pasted text
                df_trans = pd.read_csv(io.StringIO(transactions_input))
                # Validate columns
                if not all(col in df_trans.columns for col in ['Symbol', 'Date', 'Qty', 'Cash Flow']):
                    raise ValueError("Transaction data must contain 'Symbol', 'Date', 'Qty', 'Cash Flow' columns.")
                
                df_trans['Date'] = df_trans['Date'].apply(parse_date)
                df_trans['Qty'] = pd.to_numeric(df_trans['Qty'], errors='coerce')
                df_trans['Cash Flow'] = pd.to_numeric(df_trans['Cash Flow'], errors='coerce')
                
                # Drop rows with NaT dates or NaN quantities/cash flows after conversion
                initial_trans_rows = len(df_trans)
                df_trans.dropna(subset=['Date', 'Qty', 'Cash Flow'], inplace=True)
                if len(df_trans) < initial_trans_rows:
                    st.warning(f"Removed {initial_trans_rows - len(df_trans)} rows from transactions due to missing or invalid data (Date, Qty, or Cash Flow).")

                # Load current prices data from pasted text
                df_prices = pd.read_csv(io.StringIO(prices_input))
                # Validate columns
                if not all(col in df_prices.columns for col in ['Symbol', 'Current Price']):
                    raise ValueError("Current price data must contain 'Symbol', 'Current Price' columns.")
                
                prices_dict = dict(zip(df_prices['Symbol'], pd.to_numeric(df_prices['Current Price'], errors='coerce')))
                # Filter out NaN prices
                prices_dict = {k: v for k, v in prices_dict.items() if pd.notna(v)}

                st.success(f"📊 Successfully parsed {len(df_trans)} transactions and {len(prices_dict)} current prices.")
                
                st.markdown("---")
                st.subheader("Data Preview:")
                st.write("**Transactions Data:**")
                st.dataframe(df_trans.head())
                st.write("**Current Prices Data:**")
                st.dataframe(df_prices.head())
                st.markdown("---")

                calculation_date = datetime.today()
                st.info(f"Valuing current holdings as of: **{calculation_date.strftime('%Y-%m-%d')}**")
                st.markdown("---")

                # Check what transactions we have for this ticker
                ticker_transactions = df_trans[df_trans['Symbol'].str.startswith(ticker_symbol)]
                stock_transactions = ticker_transactions[~ticker_transactions['Symbol'].apply(is_option_symbol)]
                option_transactions = ticker_transactions[ticker_transactions['Symbol'].apply(is_option_symbol)]
                
                st.info(f"Summary for {ticker_symbol}:")
                st.info(f"  - {len(stock_transactions)} stock transactions")
                st.info(f"  - {len(option_transactions)} option transactions")

                st.markdown("---")

                stock_xirr = calculate_stock_only_xirr(ticker_symbol, df_trans, prices_dict, calculation_date)
                
                st.markdown("---")

                if not option_transactions.empty:
                    combined_xirr = calculate_combined_xirr(ticker_symbol, df_trans, prices_dict, calculation_date)
                    
                    if stock_xirr is not None and combined_xirr is not None:
                        st.markdown("---")
                        st.header(f"📈 Final Results for {ticker_symbol}:")
                        st.metric("Stock-Only XIRR", f"{stock_xirr:.3%}")
                        st.metric("Combined XIRR", f"{combined_xirr:.3%}")
                        st.metric("Options Impact", f"{(combined_xirr - stock_xirr):>+8.3%}")
                else:
                    st.markdown("---")
                    st.header(f"📈 Final Result for {ticker_symbol}:")
                    if stock_xirr is not None:
                        st.metric("Stock-Only XIRR", f"{stock_xirr:.3%}")
                    else:
                        st.warning("No XIRR calculated.")

            except pd.errors.EmptyDataError:
                st.error("Error: One of the pasted data areas is empty or contains no data rows (only headers).")
            except pd.errors.ParserError as pe:
                st.error(f"Error parsing CSV data. Please check for malformed lines or incorrect delimiters: {pe}")
            except ValueError as ve:
                st.error(f"Data format error: {ve}")
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}. Please ensure your data is in the correct CSV format with the specified columns and valid content.")

