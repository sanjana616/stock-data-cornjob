import os
import sqlite3
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import pytz
import yfinance as yf
import pandas as pd


# ----------------------------
# CONFIGURATIONS
# ----------------------------
DB_NAME = "nifty50_top20.db"
README_FILE = "README.md"
SYMBOLS_FILE = "symbols.json"

# IST timezone
IST = pytz.timezone("Asia/Kolkata")

# Top 20 NIFTY50 stocks (symbols must match Yahoo Finance format, ".NS" for NSE India)
STOCKS = []

# Setup logging with file size rotation
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# RotatingFileHandler: 5MB max file size, keep 5 backup files
handler = RotatingFileHandler(
    filename="data_fetch.log",
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=5  # Keep 5 backup files (data_fetch.log.1, .2, .3, .4, .5)
)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


# ----------------------------
# DATABASE FUNCTIONS
# ----------------------------
def init_db():
    """Ensure database exists with tables for each stock."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    for stock in STOCKS:
        table_name = stock.replace(".", "_")
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS '{table_name}' (
                datetime TEXT PRIMARY KEY,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER
            )
        """)
    conn.commit()
    conn.close()


def insert_data(stock, df):
    """Insert stock data into database with ON CONFLICT IGNORE to avoid duplicates."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Convert datetime column to string, removing timezone
    df["datetime"] = df["datetime"].dt.tz_localize(None).astype(str)
    df["volume"] = df["volume"].astype(int)
    print(f"My data is \n{df.tail(2)}")

    rows = df[["datetime", "open", "high", "low", "close", "volume"]].values.tolist()
    print(f"rows: {rows[-2:]}")

    cursor.executemany(
        f"""
        INSERT OR IGNORE INTO '{stock}' (datetime, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows
    )

    conn.commit()
    conn.close()
    logger.info(f"[{stock}] Inserted {len(rows)} rows (duplicates ignored)")

# ----------------------------
# DATA FETCHING
# ----------------------------
def fetch_stock_data(stock):
    """Fetch 1-min data for the past 15 minutes for a stock."""
    try:
        df = yf.download(
            tickers=stock,
            interval="1m",
            period="1d",
            progress=True)

        
        if df.empty:
            logger.warning(f"No data returned for {stock}")
            return None

        # Reset index to get datetime as column
        df = df.droplevel('Ticker', axis=1)
        df.reset_index(inplace=True)

        # Convert timezone to IST
        df["Datetime"] = df["Datetime"].dt.tz_convert(IST)
        
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0).astype(int)
        df["Volume"] = df["Volume"] * 1 

        df.rename(columns={
            "Datetime": "datetime",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume"
        }, inplace=True)

        # df['volume'] = df['volume'].apply(lambda x: int.from_bytes(x, byteorder='little', signed=False))
        
        

        return df[["datetime", "open", "high", "low", "close", "volume"]]

    except Exception as e:
        logger.error(f"Error fetching data for {stock}: {e}")
        return None


# ----------------------------
# README UPDATE
# ----------------------------
def update_readme():
    """Append last 2 rows from each stock table to README.md using HTML table, preserving the title."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Preserve the title from existing README or use default
    title = f"# 📈 {README_FILE[:-3]} Data Snapshot\n\n"
    if os.path.exists(README_FILE):
        with open(README_FILE, "r", encoding="utf-8") as f:
            first_line = f.readline()
            if first_line.startswith("# "):
                title = first_line + "\n"  # Include the newline

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(title)
        f.write(f"Last updated: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n")

        for stock in STOCKS:
            table_name = stock.replace(".", "_")
            try:
                df = pd.read_sql_query(
                    f"SELECT datetime, close, volume FROM '{table_name}' ORDER BY datetime DESC LIMIT 2", conn
                )
                if df.empty:
                    continue

                # Write HTML table
                f.write(f"## {table_name}\n\n")
                f.write('<table>\n')
                f.write('  <tr><th>Datetime</th><th>Close</th><th>Volume</th></tr>\n')
                for _, row in df.iterrows():
                    f.write(f"  <tr><td>{row['datetime']}</td><td>{row['close']}</td><td>{row['volume']}</td></tr>\n")
                f.write('</table>\n\n')
            except Exception as e:
                logger.error(f"Error updating README for {stock}: {e}")

    conn.close()


# ----------------------------
# MAIN WORKFLOW
# ----------------------------
def main():
    logger.info("Starting data fetch cycle...")
    init_db()

    for stock in STOCKS:
        df = fetch_stock_data(stock)
        if df is not None and not df.empty:
            insert_data(stock.replace('.', '_'), df)
            logger.info(f"Inserted {len(df)} rows for {stock}")
        else:
            logger.warning(f"No data to insert for {stock}")

    update_readme()
    logger.info("Cycle complete. README updated.")


if __name__ == "__main__":
    # IST timezone
    IST = pytz.timezone("Asia/Kolkata")
    SYMBOLS_FILE = "symbols.json"
    try:
        if not os.path.exists(SYMBOLS_FILE):
            raise FileNotFoundError(f"Symbols file not found: {SYMBOLS_FILE}")

        with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        symbols_key = "Mid Cap"
        stocks = data.get(symbols_key)
        if not isinstance(stocks, list) or not stocks:
            raise ValueError(f"Invalid or empty STOCKS list in {SYMBOLS_FILE} for key '{symbols_key}'")
    except Exception as e:
        logger.error(f"Error loading symbols: {e}")
        exit(1)
    STOCKS = stocks
    DB_NAME = f"{symbols_key}.db"
    README_FILE = f"README.md"
    main()