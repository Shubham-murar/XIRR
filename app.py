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
    starting_rates = [0.1, 0.01, 0.5, -0.5, 0.2, -0.2, 1.0, -0.9]
    
    for start_rate in starting_rates:
        try:
            result = newton(lambda r: xnpv(r, cashflows), start_rate, maxiter=1000, tol=1e-6)
            # Validate result is reasonable (between -99% and 10000%)
            if -0.99 <= result <= 100:
                return result
        except:
            continue
    
    return None

def calculate_stock_only_xirr(ticker, df_trans, prices_dict, calculation_date):
    """Calculate XIRR for stock transactions only (excluding options)"""
    
    # Filter for stock transactions only (not options)
    stock_trans = df_trans[
        (df_trans['Symbol'] == ticker) & 
        (~df_trans['Symbol'].apply(is_option_symbol))
    ].copy()
    
    if stock_trans.empty:
        return None, [] # Return None for XIRR and empty list for cashflows
    
    # Sort by date
    stock_trans = stock_trans.sort_values('Date')
    
    cashflows = []
    total_qty = 0
    
    for _, row in stock_trans.iterrows():
        date = row['Date']
        qty = row['Qty']
        cash_flow = row['Cash Flow']
        
        cashflows.append((date, cash_flow))
        total_qty += qty
    
    # Add terminal value for current holdings
    current_price = prices_dict.get(ticker)
    if current_price and total_qty != 0:
        terminal_value = total_qty * current_price
        cashflows.append((calculation_date, terminal_value))
    
    # Calculate XIRR
    if len(cashflows) >= 2:
        xirr = calculate_xirr(sorted(cashflows, key=lambda x: x[0]))
        return xirr, sorted(cashflows, key=lambda x: x[0])
    
    return None, []

def calculate_combined_xirr(ticker, df_trans, prices_dict, calculation_date):
    """Calculate XIRR including both stock and option transactions"""
    
    # Get all transactions for this ticker (stock and options)
    ticker_trans = df_trans[
        df_trans['Symbol'].str.startswith(ticker)
    ].copy().sort_values('Date')
    
    if ticker_trans.empty:
        return None, [] # Return None for XIRR and empty list for cashflows
    
    cashflows = []
    stock_qty = 0
    option_positions = {}
    
    for _, row in ticker_trans.iterrows():
        symbol = row['Symbol']
        date = row['Date']
        qty = row['Qty']
        cash_flow = row['Cash Flow']
        
        cashflows.append((date, cash_flow))
        
        if is_option_symbol(symbol):
            if symbol not in option_positions:
                option_positions[symbol] = {'net_qty': 0, 'transactions': []}
            
            option_positions[symbol]['net_qty'] += qty
            option_positions[symbol]['transactions'].append(row)
        else:
            stock_qty += qty
    
    # Handle current option values
    if option_positions:
        for symbol, position in option_positions.items():
            net_qty = position['net_qty']
            
            if net_qty != 0:
                try:
                    ticker_len = len(ticker)
                    date_part = symbol[ticker_len:ticker_len+6]  # YYMMDD
                    expiry = datetime.strptime(date_part, "%y%m%d")
                    
                    if expiry >= datetime.today():
                        current_option_price = prices_dict.get(symbol)
                        if current_option_price:
                            option_value = current_option_price * net_qty * 100
                            cashflows.append((calculation_date, option_value))
                except Exception:
                    # Silently skip if expiry parsing fails, as per reduced verbosity
                    pass
    
    # Add terminal value for stock holdings
    current_stock_price = prices_dict.get(ticker)
    if current_stock_price and stock_qty != 0:
        terminal_value = stock_qty * current_stock_price
        cashflows.append((calculation_date, terminal_value))
    
    # Calculate XIRR
    if len(cashflows) >= 2:
        xirr = calculate_xirr(sorted(cashflows, key=lambda x: x[0]))
        return xirr, sorted(cashflows, key=lambda x: x[0])
    
    return None, []

# --- Streamlit App Layout ---

st.set_page_config(layout="centered", page_title="Portfolio XIRR Calculator")

st.title("🚀 Portfolio XIRR Calculator")

# Info button
with st.expander("ℹ️ How to Use"):
    st.markdown("""
    Upload your **Transaction Data** and **Current Price Data** CSV files below.
    The application will automatically detect all unique stock tickers from your transaction data and calculate XIRR for each.
    
    **1. Transaction Data File Requirements:**
    * **Format:** CSV (Comma Separated Values)
    * **Required Columns:** `Symbol`, `Date`, `Qty`, `Cash Flow`
    * **Column Details:**
        * `Symbol`: Stock ticker (e.g., BKE, BOOT) or option symbol (e.g., BKE251219C00040000)
        * `Date`: Transaction date (e.g., `MM/DD/YYYY` or `DD-MM-YYYY`)
        * `Qty`: Quantity of shares/contracts (positive for buy, negative for sell)
        * `Cash Flow`: Cash amount (negative for money out, positive for money in)
    * **Example Row:** `BKE,3/18/2025,25,-939.75`
    
    **2. Current Price Data File Requirements:**
    * **Format:** CSV (Comma Separated Values)
    * **Required Columns:** `Symbol`, `Current Price`
    * **Column Details:**
        * `Symbol`: Stock ticker or option symbol
        * `Current Price`: The current price of the stock/option
    * **Example Row:** `BKE,49.7`
    
    After uploading both files, click "Calculate XIRR for All Tickers" to see the results.
    """)

st.markdown("---")

# File uploaders
transaction_file = st.file_uploader("Upload **Transaction Data** CSV File", type=["csv"])
prices_file = st.file_uploader("Upload **Current Price Data** CSV File", type=["csv"])

# Buttons for actions
col_buttons = st.columns(2)
with col_buttons[0]:
    calculate_button = st.button("Calculate XIRR for All Tickers", type="primary")
with col_buttons[1]:
    # Reset button for file uploaders is not directly supported by clearing st.session_state
    # A full rerun (st.experimental_rerun) is needed to clear file_uploader widgets
    if st.button("Reset All"):
        # This will clear the file uploaders and rerun the script
        st.experimental_rerun()

if calculate_button:
    if transaction_file is None or prices_file is None:
        st.error("Please upload both the Transaction Data file and the Current Price Data file.")
    else:
        with st.spinner("Loading and parsing data..."):
            try:
                # Load transaction data from uploaded file
                df_trans = pd.read_csv(transaction_file)
                # Validate columns
                if not all(col in df_trans.columns for col in ['Symbol', 'Date', 'Qty', 'Cash Flow']):
                    raise ValueError("Transaction data must contain 'Symbol', 'Date', 'Qty', 'Cash Flow' columns.")
                
                df_trans['Date'] = df_trans['Date'].apply(parse_date)
                df_trans['Qty'] = pd.to_numeric(df_trans['Qty'], errors='coerce')
                df_trans['Cash Flow'] = pd.to_numeric(df_trans['Cash Flow'], errors='coerce')
                
                initial_trans_rows = len(df_trans)
                df_trans.dropna(subset=['Date', 'Qty', 'Cash Flow'], inplace=True)
                if len(df_trans) < initial_trans_rows:
                    st.warning(f"Removed {initial_trans_rows - len(df_trans)} rows from transactions due to missing or invalid data (Date, Qty, or Cash Flow).")

                # Load current prices data from uploaded file
                df_prices = pd.read_csv(prices_file)
                # Validate columns
                if not all(col in df_prices.columns for col in ['Symbol', 'Current Price']):
                    raise ValueError("Current price data must contain 'Symbol', 'Current Price' columns.")
                
                prices_dict = dict(zip(df_prices['Symbol'], pd.to_numeric(df_prices['Current Price'], errors='coerce')))
                prices_dict = {k: v for k, v in prices_dict.items() if pd.notna(v)}

                st.success(f"📊 Successfully parsed {len(df_trans)} transactions and {len(prices_dict)} current prices.")
                
                st.markdown("---")
                st.subheader("Data Preview:")
                st.write("**Transactions Data (first 5 rows):**")
                st.dataframe(df_trans.head())
                st.write("**Current Prices Data (first 5 rows):**")
                st.dataframe(df_prices.head())
                st.markdown("---")

                calculation_date = datetime.today()
                st.info(f"Valuing current holdings as of: **{calculation_date.strftime('%Y-%m-%d')}**")
                st.markdown("---")

                # --- Automatic Ticker Detection ---
                base_tickers = set()
                for symbol in df_trans['Symbol'].unique():
                    if not is_option_symbol(symbol):
                        base_tickers.add(symbol)
                    else:
                        potential_base = symbol
                        match = None
                        for i, char in enumerate(symbol):
                            if char.isdigit():
                                match = i
                                break
                        if match is not None:
                            potential_base = symbol[:match]

                        if potential_base and not is_option_symbol(potential_base):
                            base_tickers.add(potential_base)
                        else:
                            longest_prefix_match = ""
                            for p_sym in prices_dict.keys():
                                if symbol.startswith(p_sym) and not is_option_symbol(p_sym) and len(p_sym) > len(longest_prefix_match):
                                    longest_prefix_match = p_sym
                            if longest_prefix_match:
                                base_tickers.add(longest_prefix_match)
                            else:
                                if not is_option_symbol(symbol):
                                    base_tickers.add(symbol)


                if not base_tickers:
                    st.warning("No recognizable stock tickers found in the transaction data for analysis.")
                    st.info("Please ensure your 'Symbol' column contains valid stock tickers or option symbols that start with a stock ticker.")
                else:
                    st.header("Results for All Detected Tickers:")
                    for ticker_symbol in sorted(list(base_tickers)):
                        st.markdown(f"## {ticker_symbol} Analysis")
                        st.markdown("---")

                        # Calculate stock-only XIRR
                        stock_xirr, stock_cashflows = calculate_stock_only_xirr(ticker_symbol, df_trans, prices_dict, calculation_date)
                        
                        if stock_cashflows:
                            st.markdown("**Stock-Only Cash Flow Summary:**")
                            df_stock_cashflows = pd.DataFrame(stock_cashflows, columns=['Date', 'Cash Flow'])
                            st.dataframe(df_stock_cashflows.style.format({'Cash Flow': '${:,.2f}'}))
                        
                        if stock_xirr is not None:
                            st.metric("Stock-Only XIRR", f"{stock_xirr:.3%}")
                        else:
                            st.info(f"Stock-Only XIRR could not be calculated for {ticker_symbol}.")

                        ticker_option_transactions = df_trans[
                            df_trans['Symbol'].str.startswith(ticker_symbol) & 
                            df_trans['Symbol'].apply(is_option_symbol)
                        ]

                        if not ticker_option_transactions.empty:
                            combined_xirr, combined_cashflows = calculate_combined_xirr(ticker_symbol, df_trans, prices_dict, calculation_date)
                            
                            if combined_cashflows:
                                st.markdown("**Combined Cash Flow Summary:**")
                                df_combined_cashflows = pd.DataFrame(combined_cashflows, columns=['Date', 'Cash Flow'])
                                st.dataframe(df_combined_cashflows.style.format({'Cash Flow': '${:,.2f}'}))

                            if combined_xirr is not None:
                                st.metric("Combined XIRR", f"{combined_xirr:.3%}")
                                if stock_xirr is not None:
                                    st.metric("Options Impact", f"{(combined_xirr - stock_xirr):>+8.3%}")
                            else:
                                st.info(f"Combined XIRR could not be calculated for {ticker_symbol} (likely insufficient data for options).")
                        else:
                            st.info(f"No option transactions found for {ticker_symbol}. Combined XIRR is not applicable.")
                        
                        st.markdown("---")

            except pd.errors.EmptyDataError:
                st.error("Error: One of the uploaded files is empty or contains no data rows (only headers).")
            except pd.errors.ParserError as pe:
                st.error(f"Error parsing CSV data. Please check for malformed lines or incorrect delimiters in your files: {pe}")
            except ValueError as ve:
                st.error(f"Data format error: {ve}")
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}. Please ensure your files are in the correct CSV format with the specified columns and valid content.")





