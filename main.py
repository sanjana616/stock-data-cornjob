import os
import sqlite3
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
import pytz
import yfinance as yf
import pandas as pd
import pandas_ta as ta


# ----------------------------
# CONFIGURATIONS
# ----------------------------
DB_NAME = "nifty50_top20.db"
README_FILE = "README.md"
SYMBOLS_FILE = "symbols.json"

# IST timezone
IST = pytz.timezone("Asia/Kolkata")

STOCKS = []
INDEXES = []

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
# HELPERS
# ----------------------------
def sanitize(symbol):
    """Convert symbol to a safe SQLite table name."""
    return symbol.replace("^", "").replace(".", "_")


# ----------------------------
# DATABASE FUNCTIONS
# ----------------------------
def init_db():
    """Ensure database exists with tables for each stock and index."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    for symbol in STOCKS + INDEXES:
        table_name = sanitize(symbol)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS '{table_name}' (
                datetime TEXT PRIMARY KEY,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                rsi_14 REAL,
                ema_20 REAL,
                macd REAL,
                macd_signal REAL,
                vwap REAL
            )
        """)
    conn.commit()
    conn.close()


def insert_data(table_name, df):
    """Insert OHLCV + indicator data into database, ignoring duplicates."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    df["datetime"] = df["datetime"].dt.tz_localize(None).astype(str)
    df["volume"] = df["volume"].astype(int)

    cols = ["datetime", "open", "high", "low", "close", "volume",
            "rsi_14", "ema_20", "macd", "macd_signal", "vwap"]

    # Fill missing indicator columns with None
    for col in cols:
        if col not in df.columns:
            df[col] = None

    rows = df[cols].values.tolist()

    cursor.executemany(
        f"""
        INSERT OR IGNORE INTO '{table_name}'
        (datetime, open, high, low, close, volume, rsi_14, ema_20, macd, macd_signal, vwap)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows
    )

    conn.commit()
    conn.close()
    logger.info(f"[{table_name}] Inserted {len(rows)} rows (duplicates ignored)")


# ----------------------------
# INDICATORS
# ----------------------------
def compute_indicators(df, is_index=False):
    """Calculate RSI, EMA20, MACD, VWAP and add as columns to df."""
    try:
        df.ta.rsi(length=14, append=True)
        df.ta.ema(length=20, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)

        # VWAP is meaningless for indexes (volume=0), skip it
        if not is_index:
            df.ta.vwap(append=True)
        else:
            df["VWAP_D"] = None

        # Normalize column names
        df.rename(columns={
            "RSI_14": "rsi_14",
            "EMA_20": "ema_20",
            "MACD_12_26_9": "macd",
            "MACDs_12_26_9": "macd_signal",
            "VWAP_D": "vwap"
        }, inplace=True)

    except Exception as e:
        logger.error(f"Error computing indicators: {e}")
        for col in ["rsi_14", "ema_20", "macd", "macd_signal", "vwap"]:
            if col not in df.columns:
                df[col] = None

    return df


# ----------------------------
# SIGNALS
# ----------------------------
def generate_signal(row):
    """Generate BUY / SELL / HOLD based on EMA20, RSI, MACD."""
    try:
        close = row["close"]
        ema20 = row["ema_20"]
        rsi = row["rsi_14"]
        macd = row["macd"]
        macd_signal = row["macd_signal"]

        if pd.isna(close) or pd.isna(ema20) or pd.isna(rsi) or pd.isna(macd) or pd.isna(macd_signal):
            return "HOLD"

        if close > ema20 and rsi > 50 and macd > macd_signal:
            return "BUY"
        elif close < ema20 and rsi < 50 and macd < macd_signal:
            return "SELL"
        return "HOLD"
    except Exception:
        return "HOLD"


# ----------------------------
# DATA FETCHING
# ----------------------------
def fetch_stock_data(symbol, is_index=False):
    """Fetch 1-min OHLCV data for the day and compute indicators."""
    try:
        df = yf.download(
            tickers=symbol,
            interval="1m",
            period="1d",
            progress=False
        )

        if df.empty:
            logger.warning(f"No data returned for {symbol}")
            return None

        df = df.droplevel("Ticker", axis=1)
        df.reset_index(inplace=True)

        df["Datetime"] = df["Datetime"].dt.tz_convert(IST)
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0).astype(int)

        df.rename(columns={
            "Datetime": "datetime",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume"
        }, inplace=True)

        df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
        df = compute_indicators(df, is_index=is_index)

        return df

    except Exception as e:
        logger.error(f"Error fetching data for {symbol}: {e}")
        return None


# ----------------------------
# README UPDATE
# ----------------------------
def update_readme():
    """Write README with latest row per symbol, split into INDEXES and STOCKS sections."""
    conn = sqlite3.connect(DB_NAME)

    title = f"# 📈 {README_FILE[:-3]} Data Snapshot\n\n"
    if os.path.exists(README_FILE):
        with open(README_FILE, "r", encoding="utf-8") as f:
            first_line = f.readline()
            if first_line.startswith("# "):
                title = first_line + "\n"

    header = "  <tr><th>Symbol</th><th>Datetime</th><th>Close</th><th>Volume</th><th>RSI</th><th>EMA20</th><th>MACD</th><th>VWAP</th><th>Signal</th></tr>\n"

    def build_rows(symbols):
        rows_html = ""
        for symbol in symbols:
            table_name = sanitize(symbol)
            try:
                df = pd.read_sql_query(
                    f"""SELECT datetime, close, volume, rsi_14, ema_20, macd, macd_signal, vwap
                        FROM '{table_name}' ORDER BY datetime DESC LIMIT 1""",
                    conn
                )
                if df.empty:
                    continue

                row = df.iloc[0]
                signal = generate_signal(row)

                def fmt(val, dec=2):
                    return f"{val:.{dec}f}" if pd.notna(val) else "-"

                rows_html += (
                    f"  <tr>"
                    f"<td>{table_name}</td>"
                    f"<td>{row['datetime']}</td>"
                    f"<td>{fmt(row['close'])}</td>"
                    f"<td>{int(row['volume']) if pd.notna(row['volume']) else '-'}</td>"
                    f"<td>{fmt(row['rsi_14'])}</td>"
                    f"<td>{fmt(row['ema_20'])}</td>"
                    f"<td>{fmt(row['macd'])}</td>"
                    f"<td>{fmt(row['vwap'])}</td>"
                    f"<td>{signal}</td>"
                    f"</tr>\n"
                )
            except Exception as e:
                logger.error(f"Error reading README data for {symbol}: {e}")
        return rows_html

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(title)
        f.write(f"Last updated: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n")

        # MARKET INDEXES section
        f.write("## 📊 MARKET INDEXES\n\n")
        f.write("<table>\n")
        f.write(header)
        f.write(build_rows(INDEXES))
        f.write("</table>\n\n")

        # STOCKS section
        f.write("## 📈 STOCKS\n\n")
        f.write("<table>\n")
        f.write(header)
        f.write(build_rows(STOCKS))
        f.write("</table>\n\n")

    conn.close()


# ----------------------------
# MAIN WORKFLOW
# ----------------------------
def main():
    logger.info("Starting data fetch cycle...")
    init_db()

    for symbol in INDEXES:
        df = fetch_stock_data(symbol, is_index=True)
        if df is not None and not df.empty:
            insert_data(sanitize(symbol), df)
            logger.info(f"Inserted {len(df)} rows for {symbol}")
        else:
            logger.warning(f"No data to insert for {symbol}")

    for symbol in STOCKS:
        df = fetch_stock_data(symbol, is_index=False)
        if df is not None and not df.empty:
            insert_data(sanitize(symbol), df)
            logger.info(f"Inserted {len(df)} rows for {symbol}")
        else:
            logger.warning(f"No data to insert for {symbol}")

    update_readme()
    logger.info("Cycle complete. README updated.")


if __name__ == "__main__":
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
            raise ValueError(f"Invalid or empty list in {SYMBOLS_FILE} for key '{symbols_key}'")

        indexes = data.get("Indexes")
        if not isinstance(indexes, list) or not indexes:
            raise ValueError(f"Invalid or empty list in {SYMBOLS_FILE} for key 'Indexes'")

    except Exception as e:
        logger.error(f"Error loading symbols: {e}")
        exit(1)

    STOCKS = stocks
    INDEXES = indexes
    DB_NAME = f"{symbols_key}.db"
    README_FILE = "README.md"
    main()
