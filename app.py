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
    Paste your transaction data and current price data into the respective text areas below.
    The application will automatically detect all unique stock tickers and calculate XIRR for each.
    
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
    
    Click "Calculate XIRR" to see the results for all detected tickers.
    """)

st.markdown("---")

# Initialize session state for inputs if not already present
if 'transactions_input' not in st.session_state:
    st.session_state.transactions_input = "Symbol,Date,Qty,Cash Flow\nBKE,3/18/2025,25,-939.75\nBKE,3/24/2025,25,-985.5\nBKE,3/28/2025,25,-942.8\nBKE,4/2/2025,25,-950.75\nBKE,4/4/2025,30,-1031.7\nBKE,4/29/2025,1.315,-45.5\nBKE,4/30/2025,-30,1032.57\nBKE250417C00040000,4/2/2025,-1,51\nBKE250417C00040000,4/17/2025,1,0\nBKE250620C00040000,4/23/2025,-1,89\nBKE250620C00040000,6/20/2025,1,-521\nBKE251219C00040000,6/23/2022,-1,649\nBOOT,2/6/2025,3,-435.96\nBOOT,2/13/2025,4,-581.28\nBOOT,2/19/2025,3,-436.5\nBOOT,2/26/2025,3,-437.55\nBOOT,3/4/2025,3,-438.27\nBOOT,3/11/2025,3,-439.86\nBOOT,3/18/2025,3,-440.13\nBOOT,3/25/2025,3,-440.73\nBOOT,4/1/2025,3,-441.36\nBOOT,4/8/2025,3,-442.23\nBOOT,4/15/2025,3,-443.1\nBOOT,4/22/2025,3,-443.97\nBOOT,4/29/2025,3,-444.84\nBOOT,5/6/2025,3,-445.71\nBOOT,5/13/2025,3,-446.58\nBOOT,5/20/2025,3,-447.45\nBOOT,5/27/2025,3,-448.32\nBOOT,6/3/2025,3,-449.19\nBOOT,6/10/2025,3,-450.06\nBOOT,6/17/2025,3,-450.93\nBOOT,6/24/2025,3,-451.8\nBOOT,7/1/2025,3,-452.67\nBOOT,7/8/2025,3,-453.54\nBOOT,7/15/2025,3,-454.41\nBOOT,7/22/2025,3,-455.28\nBOOT,7/29/2025,3,-456.15\nBOOT,8/5/2025,3,-457.02\nBOOT,8/12/2025,3,-457.89\nBOOT,8/19/2025,3,-458.76\nBOOT,8/26/2025,3,-459.63\nBOOT,9/2/2025,3,-460.5\nBOOT,9/9/2025,3,-461.37\nBOOT,9/16/2025,3,-462.24\nBOOT,9/23/2025,3,-463.11\nBOOT,9/30/2025,3,-463.98\nBOOT,10/7/2025,3,-464.85\nBOOT,10/14/2025,3,-465.72\nBOOT,10/21/2025,3,-466.59\nBOOT,10/28/2025,3,-467.46\nBOOT,11/4/2025,3,-468.33\nBOOT,11/11/2025,3,-469.2\nBOOT,11/18/2025,3,-470.07\nBOOT,11/25/2025,3,-470.94\nBOOT,12/2/2025,3,-471.81\nBOOT,12/9/2025,3,-472.68\nBOOT,12/16/2025,3,-473.55\nBOOT,12/23/2025,3,-474.42\nBOOT,12/30/2025,3,-475.29\n"
if 'prices_input' not in st.session_state:
    st.session_state.prices_input = "Symbol,Current Price\nBKE,49.7\nBOOT,170\nBKE251219C00040000,11.2\n"

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

# Removed ticker_symbol input as it will be auto-detected

# Buttons for actions
col_buttons = st.columns(2)
with col_buttons[0]:
    calculate_button = st.button("Calculate XIRR for All Tickers", type="primary")
with col_buttons[1]:
    if st.button("Reset All"):
        st.session_state.transactions_input = "Symbol,Date,Qty,Cash Flow\n"
        st.session_state.prices_input = "Symbol,Current Price\n"
        st.experimental_rerun() # Rerun to clear inputs

if calculate_button:
    if not transactions_input.strip() or not prices_input.strip():
        st.error("Please paste both transaction and current price data.")
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

                # --- Automatic Ticker Detection ---
                # Extract base tickers (remove option details)
                base_tickers = set()
                for symbol in df_trans['Symbol'].unique():
                    if not is_option_symbol(symbol):
                        base_tickers.add(symbol)
                    else:
                        # Attempt to extract base ticker from option symbol, e.g., BKE from BKE251219C00040000
                        # This is a heuristic and might need adjustment based on actual symbol formats
                        # For now, let's try to find the longest prefix that is a known stock symbol
                        # or derive it if it's not directly in prices_dict but is a valid stock-like symbol
                        potential_base = symbol
                        # Try to strip date/strike from option symbol to get potential base ticker
                        # Example: BKE251219C00040000 -> BKE
                        # This is a simplified heuristic and might not cover all cases.
                        # A more robust solution would involve a list of known stock tickers.
                        
                        # Find the first digit sequence after the initial alpha characters, assuming it's the date
                        match = None
                        for i, char in enumerate(symbol):
                            if char.isdigit():
                                match = i
                                break
                        if match is not None:
                            potential_base = symbol[:match]

                        # Check if this potential_base is a stock symbol (not an option itself)
                        if potential_base and not is_option_symbol(potential_base):
                            base_tickers.add(potential_base)
                        else:
                            # Fallback if the above heuristic fails or yields an option-like symbol
                            # Try to find the longest prefix that exists in prices_dict as a non-option
                            longest_prefix_match = ""
                            for p_sym in prices_dict.keys():
                                if symbol.startswith(p_sym) and not is_option_symbol(p_sym) and len(p_sym) > len(longest_prefix_match):
                                    longest_prefix_match = p_sym
                            if longest_prefix_match:
                                base_tickers.add(longest_prefix_match)
                            else:
                                # If still no match, just add the symbol itself if it's not an option
                                if not is_option_symbol(symbol):
                                    base_tickers.add(symbol)


                if not base_tickers:
                    st.warning("No recognizable stock tickers found in the transaction data for analysis.")
                    st.info("Please ensure your 'Symbol' column contains valid stock tickers or option symbols that start with a stock ticker.")
                else: # Only proceed with analysis if base_tickers is not empty
                    st.header("Results for All Detected Tickers:")
                    for ticker_symbol in sorted(list(base_tickers)):
                        st.markdown(f"## {ticker_symbol} Analysis")
                        st.markdown("---")

                        # Calculate stock-only XIRR
                        stock_xirr, stock_cashflows = calculate_stock_only_xirr(ticker_symbol, df_trans, prices_dict, calculation_date)
                        
                        if stock_cashflows: # Only display if there are cashflows
                            st.markdown("**Stock-Only Cash Flow Summary:**")
                            df_stock_cashflows = pd.DataFrame(stock_cashflows, columns=['Date', 'Cash Flow'])
                            st.dataframe(df_stock_cashflows.style.format({'Cash Flow': '${:,.2f}'}))
                        
                        if stock_xirr is not None:
                            st.metric("Stock-Only XIRR", f"{stock_xirr:.3%}")
                        else:
                            st.info(f"Stock-Only XIRR could not be calculated for {ticker_symbol}.")

                        # Check if options exist for this ticker before calculating combined XIRR
                        ticker_option_transactions = df_trans[
                            df_trans['Symbol'].str.startswith(ticker_symbol) & 
                            df_trans['Symbol'].apply(is_option_symbol)
                        ]

                        if not ticker_option_transactions.empty:
                            combined_xirr, combined_cashflows = calculate_combined_xirr(ticker_symbol, df_trans, prices_dict, calculation_date)
                            
                            if combined_cashflows: # Only display if there are cashflows
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
                        
                        st.markdown("---") # Separator between tickers

            except pd.errors.EmptyDataError:
                st.error("Error: One of the pasted data areas is empty or contains no data rows (only headers).")
            except pd.errors.ParserError as pe:
                st.error(f"Error parsing CSV data. Please check for malformed lines or incorrect delimiters: {pe}")
            except ValueError as ve:
                st.error(f"Data format error: {ve}")
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}. Please ensure your data is in the correct CSV format with the specified columns and valid content.")

