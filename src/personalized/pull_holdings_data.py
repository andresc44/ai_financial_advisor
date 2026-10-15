# File to pull portfolio data using snaptrade api and convert to pandas dataframe for analysis and reporting.
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import pandas as pd
import yfinance as yf
import io
import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from dotenv import load_dotenv
from snaptrade_client.client import SnapTrade
from snaptrade_client.auth import SnapTradeAuth

# Claude-authored (2026-10-13): imports switched to the src.* convention used elsewhere in the repo.
# Why: the bare `from user_profiles import ...` form only worked when run from src/personalized, so the
# watchdog (src/telegram_alert) could not import this module from the project root under cron.
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.personalized.user_profiles import USER_PROFILES
from src.personalized.add_metrics import add_metrics_to_holdings
from src.personalized.recommendation import add_recommendations
from src.personalized.user_settings import get_claude_overrides
from src.personalized.digest_fields import add_digest_fields, portfolio_beta
from src.market_session import exchange_calendar_code, latest_regular_session


def _price_column(data: pd.DataFrame, field: str, symbol: str) -> Optional[pd.Series]:
    """
    Claude-authored (2026-10-15): one symbol's column (e.g. "Close") from a yf.download frame. yfinance 1.x returns
    per-ticker columns even for a single ticker, and older versions a plain column; both are handled here.
    """
    if field not in data:
        return None
    column = data[field]
    if isinstance(column, pd.Series):
        return column
    return column[symbol] if symbol in column.columns else None


class PortfolioFetcher:
    add_metrics_to_holdings = add_metrics_to_holdings #method to append online data to the holdings dataframe
    add_recommendations = add_recommendations #method to evaluate hold positions and add recommendations to the holdings dataframe

    def __init__(self, user_name: str = "Andres", env_path: str = ".env"):
        print("Searching for user snaptrade credentials in .env file...")
        load_dotenv(dotenv_path=env_path, override=True)

        user_profile = USER_PROFILES.get(user_name)
        if user_profile is None:
            raise ValueError(f"User '{user_name}' not found in USER_PROFILES.")

        # Claude-authored (2026-10-13): remember who this is for, and their holdings-evaluation model/effort.
        # add_recommendations reads both via `self` (it is bound as a method of this class).
        self.user_name = user_name
        self.claude_overrides = get_claude_overrides(user_name)
        self.claude_key_error = None  # Set by add_recommendations when the user's Anthropic key can't be used

        holdings_source_value = user_profile.get("holdings_source")
        if holdings_source_value is None:
            raise ValueError(
                f"Holdings source for user '{user_name}' is not configured"
                " in USER_PROFILES."
            )
        self.holdings_source = os.environ.get(holdings_source_value)
        print(f"Holdings source for user '{user_name}': {self.holdings_source}")
        
        if self.holdings_source == "SNAPTRADE":
            snap_client_id_value = user_profile.get("snaptrade_client_id")
            if snap_client_id_value is None:
                raise ValueError(
                    f"SnapTrade client ID for user '{user_name}' is not configured"
                    " in USER_PROFILES."
                )
            client_id = os.environ.get(snap_client_id_value)

            snap_consumer_key_value = user_profile.get("snaptrade_consumer_key")
            if snap_consumer_key_value is None:
                raise ValueError(
                    f"SnapTrade consumer key for user '{user_name}' is not"
                    " configured in USER_PROFILES."
                )
            consumer_key = os.environ.get(snap_consumer_key_value)

            if not client_id or not consumer_key:
                raise ValueError(
                    "Missing SNAPTRADE_CLIENT_ID or SNAPTRADE_CONSUMER_KEY in .env"
                    " file."
                )

            print("Attempting to connect to SnapTrade API...")
            self.client = SnapTrade(
                auth=SnapTradeAuth.personal_api_key(
                    client_id=client_id, consumer_key=consumer_key
                )
            )
            self.full_data = self.get_holdings_df()
        elif self.holdings_source == "SHEETS":
            sheets_id_key = user_profile.get("google_sheets_id")
            sheets_id = os.environ.get(sheets_id_key)
            
            if not sheets_id:
                raise ValueError(
                    f"Google Sheets ID for user '{user_name}' is not configured in USER_PROFILES or .env."
                )

            print("Attempting to fetch holdings from Google Sheets...")
            self.full_data = self.pull_sheets_holdings(sheets_id)
        else:
            print(f"User '{user_name}' has no holdings source configured. Exiting with empty DataFrame.")
            self.full_data = pd.DataFrame()  # Empty DataFrame for users with no holdings source

    def _to_dict(self, item):
        """Safely converts SDK model objects or standard dictionaries into standard python dicts."""
        if isinstance(item, dict):
            return item
        if hasattr(item, "to_dict"):
            return item.to_dict()
        if hasattr(item, "__dict__"):
            return item.__dict__
        return {}

    def fetch_user_accounts(self) -> list:
        try:
            response = self.client.account_information.list_user_accounts()
            accounts = (
                response.body
                if hasattr(response, "body") and isinstance(response.body, list)
                else response if isinstance(response, list) else []
            )
            return accounts
        except Exception as e:
            print(f"[ERROR] Failed to fetch accounts: {e}")
            return []

    def fetch_positions_for_account(self, account_id: str) -> tuple:
        try:
            response = (
                self.client.account_information.get_all_account_positions(
                    account_id=account_id
                )
            )
            raw = response.body if hasattr(response, "body") else response
            raw_dict = self._to_dict(raw)

            if isinstance(raw_dict, dict) and "results" in raw_dict:
                return raw_dict["results"], raw_dict.get("data_freshness", {})
            elif isinstance(raw, list):
                return raw, {}
            return [], {}
        except Exception as e:
            print(
                "[ERROR] Failed fetching positions for account ID"
                f" {account_id}: {e}"
            )
            return [], {}

    def get_holdings_df(self) -> pd.DataFrame:
        """Retrieves position metrics from SnapTrade as a pandas DataFrame."""
        accounts = self.fetch_user_accounts()
        records = []

        for acc in accounts:
            acc_dict = self._to_dict(acc)
            account_id = acc_dict.get("id")

            raw_name = acc_dict.get("name")
            raw_number = acc_dict.get("number")
            account_label = raw_name or raw_number or str(account_id)

            if not account_id:
                continue

            print(f"Processing Account: {account_label}")

            positions, freshness = self.fetch_positions_for_account(account_id)

            for pos in positions:
                pos_dict = self._to_dict(pos)

                instrument = self._to_dict(pos_dict.get("instrument", {}))
                figi = self._to_dict(instrument.get("figi_instrument", {}))

                units = float(pos_dict.get("units") or 0.0)
                price = float(pos_dict.get("price") or 0.0)
                cost_basis_per_share = float(
                    pos_dict.get("cost_basis")
                    or pos_dict.get("average_purchase_price")
                    or 0.0
                )

                market_value = units * price
                total_cost_basis = units * cost_basis_per_share
                unrealized_pnl = market_value - total_cost_basis
                unrealized_pnl_pct = (
                    ((unrealized_pnl / total_cost_basis) * 100)
                    if total_cost_basis > 0
                    else 0.0
                )

                record = {
                    "account_name": account_label,
                    "account_id": account_id,
                    "symbol": instrument.get("symbol")
                    or instrument.get("raw_symbol"),
                    "description": instrument.get("description"),
                    "asset_kind": instrument.get("kind"),
                    "exchange": instrument.get("exchange"),
                    "currency": pos_dict.get("currency")
                    or instrument.get("currency"),
                    "instrument_id": instrument.get("id"),
                    "figi_code": figi.get("figi_code"),
                    "figi_share_class": figi.get("figi_share_class"),
                    "shares": units,
                    "current_price": price,
                    "avg_cost_basis": cost_basis_per_share,
                    "market_value": market_value,
                    "total_cost_basis": total_cost_basis,
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                    "data_as_of": freshness.get("as_of"),
                }
                records.append(record)

        records_df = pd.DataFrame(records)
        df_pnl = self.calculate_daily_pnl(records_df)
        return self.add_technical_indicators(df_pnl)

    def _fetch_yfinance_daily_closes(self, symbols: list) -> dict:
        """
        Claude-authored (2026-10-15): {symbol: daily closes indexed by session date} for the last ~2 weeks, from one
        batch download. Replaces the old "second-to-last daily bar" lookup, which was only right when Yahoo had (or
        hadn't yet) created today's bar at the right moment, and failed for a portfolio with a single symbol.
        """
        closes = {}
        sym_map = {
            s: s.replace(":", "-")
            for s in set(symbols)
            if isinstance(s, str) and s
        }
        if not sym_map:
            return closes

        print(f"Batch fetching daily closes for {len(sym_map)} symbols...")
        try:
            data = yf.download(
                tickers=sorted(set(sym_map.values())),
                period="10d",  # Covers long weekends and holidays on either exchange
                interval="1d",
                progress=False,
                threads=True,
            )
        except Exception as e:
            print(f"[ERROR] Batch download failed: {e}")
            return closes

        for orig_sym, sym in sym_map.items():
            series = _price_column(data, "Close", sym)
            if series is None:
                continue
            series = series.dropna()
            if not series.empty:
                series.index = pd.DatetimeIndex(series.index).date  # Exchange-local session dates
                closes[orig_sym] = series
        return closes

    def calculate_daily_pnl(self, df: pd.DataFrame = None) -> pd.DataFrame:
        """
        Fetches daily closes from yfinance and calculates daily profit/loss.

        Claude-authored (2026-10-15): daily P/L is the move over each stock's most recent regular session on its
        own exchange (XNYS, or XTSE for Canadian listings), and `daily_pnl_session` holds that session's date:
        - from the open until the close: today's session, live current_price vs the previous session's close;
        - after the close, overnight, pre-market, weekends, and holidays: the last completed session, measured at
          its official close vs the close before it, so after-hours or pre-market broker prices never leak in
          (the watchdog tracks extended-hours moves separately with session_price).
        `last_close` is that session's previous close. Stocks without Yahoo data keep the old fallback of 0 P/L.
        """
        if df is None:
            df = self.get_holdings_df()

        if df.empty:
            print(
                "[INFO] DataFrame is empty. Skipping daily P&L calculation."
            )
            return df

        df = df.copy()

        symbols = [
            s for s in df["symbol"].dropna().unique() if isinstance(s, str)
        ]
        daily_closes = self._fetch_yfinance_daily_closes(symbols)
        now = datetime.now(timezone.utc)

        session_dates, prev_closes, session_closes = {}, {}, {}
        for symbol in symbols:
            session_date, in_progress = latest_regular_session(now, exchange_calendar_code(symbol))
            session_dates[symbol] = session_date.isoformat()
            closes = daily_closes.get(symbol)
            if closes is None:
                continue
            earlier = closes[closes.index < session_date]
            if not earlier.empty:
                prev_closes[symbol] = float(earlier.iloc[-1])
            if not in_progress and session_date in closes.index:
                session_closes[symbol] = float(closes[session_date])

        df["daily_pnl_session"] = df["symbol"].map(session_dates)
        df["last_close"] = pd.to_numeric(df["symbol"].map(prev_closes), errors="coerce")
        df["last_close"] = df["last_close"].fillna(df["current_price"])
        pnl_price = pd.to_numeric(df["symbol"].map(session_closes), errors="coerce").fillna(df["current_price"])

        df["daily_pnl"] = (pnl_price - df["last_close"]) * df["shares"]
        df["daily_pnl_pct"] = (
            (pnl_price - df["last_close"]) / df["last_close"] * 100
        ).where(df["last_close"] > 0, 0.0)

        total_daily_pnl = df["daily_pnl"].sum()
        total_prev_market_value = (df["last_close"] * df["shares"]).sum()
        total_daily_pnl_pct = (
            ((total_daily_pnl / total_prev_market_value) * 100)
            if total_prev_market_value > 0
            else 0.0
        )

        print("\n================ DAILY P&L SUMMARY ================")
        print(f"Total Portfolio Daily P&L ($): ${total_daily_pnl:+,.2f}")
        print(f"Total Portfolio Daily P&L (%): {total_daily_pnl_pct:+.2f}%")
        print("====================================================\n")

        return df

    def add_technical_indicators(
        self,
        df: pd.DataFrame,
        length_rsi: int = 14,
        length_natr: int = 14,
        length_vol: int = 30,
        period: str = "6mo",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Calculates RSI, NATR, and Annualized Volatility (%) using pure pandas."""
        if df is None or df.empty:
            return df

        df = df.copy()
        symbols = [
            s for s in df["symbol"].dropna().unique() if isinstance(s, str)
        ]
        if not symbols:
            return df

        sym_map = {s: s.replace(":", "-") for s in symbols}
        formatted_symbols = list(sym_map.values())

        print(
            "Fetching technical indicators (RSI, NATR, Volatility) for"
            f" {len(formatted_symbols)} symbols..."
        )

        rsi_dict = {}
        natr_dict = {}
        vol_dict = {}

        try:
            hist_data = yf.download(
                tickers=formatted_symbols,
                period=period,
                interval=interval,
                progress=False,
                threads=True,
            )

            for orig_sym, sym in sym_map.items():
                try:
                    # Claude-authored (2026-10-15): one code path for one or many tickers. The old single-ticker
                    # branch got per-ticker columns from yfinance 1.x and failed (RSI/NATR/volatility all NaN).
                    columns = {field: _price_column(hist_data, field, sym) for field in ("High", "Low", "Close")}
                    if any(column is None for column in columns.values()):
                        continue
                    ticker_df = pd.DataFrame(columns).dropna()

                    min_required = max(length_rsi, length_natr, length_vol)
                    if len(ticker_df) >= min_required:
                        # 1. Pure Pandas RSI (Wilder's Smoothing)
                        delta = ticker_df["Close"].diff()
                        gain = delta.clip(lower=0)
                        loss = -delta.clip(upper=0)
                        avg_gain = gain.ewm(
                            alpha=1 / length_rsi, adjust=False
                        ).mean()
                        avg_loss = loss.ewm(
                            alpha=1 / length_natr, adjust=False
                        ).mean()
                        rs = avg_gain / avg_loss
                        rsi_series = 100 - (100 / (1 + rs))

                        # 2. Pure Pandas NATR (Normalized ATR = ATR / Close * 100)
                        high_low = ticker_df["High"] - ticker_df["Low"]
                        high_close = (
                            ticker_df["High"] - ticker_df["Close"].shift(1)
                        ).abs()
                        low_close = (
                            ticker_df["Low"] - ticker_df["Close"].shift(1)
                        ).abs()
                        tr = pd.concat(
                            [high_low, high_close, low_close], axis=1
                        ).max(axis=1)
                        atr_series = tr.ewm(
                            alpha=1 / length_natr, adjust=False
                        ).mean()
                        natr_series = (atr_series / ticker_df["Close"]) * 100

                        # 3. Pure Pandas Annualized Historical Volatility (%)
                        daily_returns = ticker_df["Close"].pct_change()
                        vol_series = (
                            daily_returns.rolling(window=length_vol).std()
                            * math.sqrt(252)
                            * 100
                        )

                        if not rsi_series.dropna().empty:
                            rsi_dict[orig_sym] = float(
                                rsi_series.dropna().iloc[-1]
                            )
                        if not natr_series.dropna().empty:
                            natr_dict[orig_sym] = float(
                                natr_series.dropna().iloc[-1]
                            )
                        if not vol_series.dropna().empty:
                            vol_dict[orig_sym] = float(
                                vol_series.dropna().iloc[-1]
                            )
                except Exception as e:
                    print(
                        f"[WARNING] Could not calculate indicators for"
                        f" {orig_sym}: {e}"
                    )

        except Exception as e:
            print(
                "[ERROR] Failed downloading historical data for indicators:"
                f" {e}"
            )

        df["rsi"] = df["symbol"].map(rsi_dict)
        df["natr"] = df["symbol"].map(natr_dict)
        df["annualized_vol"] = df["symbol"].map(vol_dict)

        return df

    def simplify_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filters the holdings DataFrame down to core P&L and technical indicator columns."""
        target_cols = [
            "symbol",
            "shares",
            "current_price",
            "last_close",
            "daily_pnl",
            "daily_pnl_pct",
            "unrealized_pnl",
            "unrealized_pnl_pct",
            "rsi",
            "natr",
            "annualized_vol",
        ]

        if df.empty:
            return pd.DataFrame(columns=target_cols)

        cols = [c for c in target_cols if c in df.columns]
        return df[cols].copy()


    def _fetch_yfinance_metadata(self, symbols: list) -> dict:
        """Fetches current price, description, and exchange for a list of symbols from yfinance."""
        metadata = {}
        if not symbols:
            return metadata

        print(
            f"Fetching live prices, descriptions, and exchanges from yfinance for"
            f" {len(symbols)} symbols..."
        )

        for sym in set(symbols):
            if not isinstance(sym, str) or not sym:
                continue

            formatted_sym = sym.replace(":", "-")
            try:
                ticker = yf.Ticker(formatted_sym)
                info = ticker.info

                # Extract price with fallbacks
                price = (
                    info.get("currentPrice")
                    or info.get("regularMarketPrice")
                    or info.get("previousClose")
                    or 0.0
                )

                # Extract description and exchange
                description = (
                    info.get("longName") or info.get("shortName") or ""
                )
                exchange = (
                    info.get("exchange") or info.get("fullExchangeName") or ""
                )

                metadata[sym] = {
                    "current_price": float(price),
                    "description": description,
                    "exchange": exchange,
                }
            except Exception as e:
                print(
                    f"[WARNING] Could not fetch yfinance metadata for {sym}: {e}"
                )
                metadata[sym] = {
                    "current_price": 0.0,
                    "description": "",
                    "exchange": "",
                }

        return metadata


    def pull_sheets_holdings(self, sheets_id: str, gid: str = "0") -> pd.DataFrame:
        """Fetches holdings data from Google Sheets using exact column mappings,
        automatically populates market data from Yahoo Finance, and calculates P&L."""
        url = f"https://docs.google.com/spreadsheets/d/{sheets_id}/export?format=csv&gid={gid}"
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        print("creds_path:", creds_path)
        # Claude-authored (2026-10-13): guard added - os.path.exists(None) raised TypeError when the env var was unset
        print("os.path.exists(creds_path):", bool(creds_path) and os.path.exists(creds_path))
        try:
            if creds_path and os.path.exists(creds_path):
                print(f"Authenticating via Google Service Account JSON: {creds_path}")
                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets.readonly",
                    "https://www.googleapis.com/auth/drive.readonly",
                ]
                creds = service_account.Credentials.from_service_account_file(
                    creds_path, scopes=scopes
                )
                creds.refresh(Request())

                headers = {"Authorization": f"Bearer {creds.token}"}
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                raw_df = pd.read_csv(io.StringIO(response.text))
            else:
                print(f"Fetching public Google Sheets CSV URL: {url}")
                raw_df = pd.read_csv(url)

        except Exception as e:
            print(f"[ERROR] Failed to download or parse Google Sheet: {e}")
            return pd.DataFrame()

        if raw_df.empty:
            print("[WARNING] Google Sheet returned no data.")
            return pd.DataFrame()

        # Validate required columns
        required_cols = ["symbol", "shares_qty", "avg_purchase_price"]
        missing_cols = [c for c in required_cols if c not in raw_df.columns]
        if missing_cols:
            raise ValueError(
                f"[ERROR] Google Sheet is missing required columns: {missing_cols}"
            )

        # Map Google Sheet inputs to internal DataFrame schema
        df = pd.DataFrame()
        df["symbol"] = raw_df["symbol"].astype(str).str.strip()
        df["shares"] = (
            pd.to_numeric(raw_df["shares_qty"], errors="coerce").fillna(0.0)
        )
        df["avg_cost_basis"] = (
            pd.to_numeric(raw_df["avg_purchase_price"], errors="coerce").fillna(0.0)
        )

        # Fetch live current_price, description, and exchange from yfinance
        symbols = df["symbol"].unique().tolist()
        yf_meta = self._fetch_yfinance_metadata(symbols)

        df["current_price"] = df["symbol"].map(
            lambda s: yf_meta.get(s, {}).get("current_price", 0.0)
        )
        df["description"] = df["symbol"].map(
            lambda s: yf_meta.get(s, {}).get("description", "")
        )
        df["exchange"] = df["symbol"].map(
            lambda s: yf_meta.get(s, {}).get("exchange", "")
        )

        # Optional metadata defaults
        df["account_name"] = raw_df.get("account_name", "Google Sheets Account")
        df["account_id"] = "SHEETS_ACC"
        df["asset_kind"] = "EQUITY"
        df["currency"] = raw_df.get("currency", "USD")
        df["instrument_id"] = ""
        df["figi_code"] = ""
        df["figi_share_class"] = ""
        df["data_as_of"] = pd.Timestamp.now().isoformat()

        # Compute monetary position metrics
        df["market_value"] = df["shares"] * df["current_price"]
        df["total_cost_basis"] = df["shares"] * df["avg_cost_basis"]
        df["unrealized_pnl"] = df["market_value"] - df["total_cost_basis"]
        df["unrealized_pnl_pct"] = df.apply(
            lambda r: (
                ((r["unrealized_pnl"] / r["total_cost_basis"]) * 100)
                if r["total_cost_basis"] > 0
                else 0.0
            ),
            axis=1,
        )

        df_pnl = self.calculate_daily_pnl(df)
        return self.add_technical_indicators(df_pnl)

    # ------------------------------------------------------------------------------------------
    # Claude-authored (2026-10-13): session-aware prices for the watchdog.
    # Why: outside regular hours, current_price (SnapTrade / yfinance info) can be the last regular-session
    # price, so the watchdog's change checks would compare a price to itself and never fire. In pre-market
    # and after-hours we use the latest extended-hours trade instead, and record how old the price is.
    # ------------------------------------------------------------------------------------------
    def _fetch_extended_hours_prices(self, symbols: list) -> dict:
        """Latest extended-hours trade per symbol from 1-minute yfinance bars: {symbol: (price, utc_timestamp)}."""
        sym_map = {s: s.replace(":", "-") for s in set(symbols) if isinstance(s, str) and s}
        if not sym_map:
            return {}
        formatted_symbols = sorted(set(sym_map.values()))

        try:
            data = yf.download(
                tickers=formatted_symbols,
                period="5d",
                interval="1m",
                prepost=True,
                progress=False,
                threads=True,
            )
            close_df = data["Close"]
        except (KeyError, ValueError, OSError) as e:
            print(f"[WARNING] Extended-hours price download failed: {e}")
            return {}

        if isinstance(close_df, pd.Series):
            close_df = close_df.to_frame(name=formatted_symbols[0])

        prices = {}
        for orig_sym, sym in sym_map.items():
            if sym not in close_df.columns:
                continue
            series = close_df[sym].dropna()
            if series.empty:
                continue
            ts = pd.Timestamp(series.index[-1])
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            prices[orig_sym] = (float(series.iloc[-1]), ts)
        return prices

    def add_session_prices(self, df: pd.DataFrame, market_phase: str) -> pd.DataFrame:
        """
        Adds session_price, session_price_as_of (UTC), and session_price_age_minutes for the given
        market phase (see src/market_session.py). Pre-market / after-hours use extended-hours trades;
        regular, overnight, and closed phases use current_price. Falls back to current_price per symbol.
        """
        if df is None or df.empty:
            return df

        df = df.copy()
        now_utc = pd.Timestamp.now(tz="UTC")
        df["session_price"] = df["current_price"]
        if "data_as_of" in df.columns:
            df["session_price_as_of"] = pd.to_datetime(df["data_as_of"], utc=True, errors="coerce")
        else:
            df["session_price_as_of"] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")

        if market_phase in ("pre_market", "after_hours"):
            extended = self._fetch_extended_hours_prices(df["symbol"].dropna().unique().tolist())
            has_price = df["symbol"].isin(extended.keys())
            df.loc[has_price, "session_price"] = df.loc[has_price, "symbol"].map(lambda s: extended[s][0])
            df.loc[has_price, "session_price_as_of"] = df.loc[has_price, "symbol"].map(lambda s: extended[s][1])
            print(f"Extended-hours prices found for {int(has_price.sum())}/{len(df)} positions.")

        df["session_price_as_of"] = df["session_price_as_of"].fillna(now_utc)
        df["session_price_age_minutes"] = (
            (now_utc - df["session_price_as_of"]).dt.total_seconds() / 60
        ).clip(lower=0).round(1)  # Sheets' data_as_of is naive local time, which can read as slightly "future"
        return df

    def get_holdings_digest_payload(
        self,
        desired_columns: list[str] = None,
        symbols: list[str] = None,
        reuse_recent_evaluations: bool = False,
    ) -> dict:
        """Return a dictionary payload containing the holdings data filtered to desired columns.

        Claude-authored (2026-10-13): optional `symbols` limits enrichment and Claude evaluation to those
        symbols (the watchdog passes only stocks that changed). None keeps the original behaviour (all holdings).
        2026-10-15: `reuse_recent_evaluations` (used by the digest) keeps evaluations made within the last
        DIGEST.REUSE_EVALUATION_MAX_AGE_MINUTES for stocks that haven't moved, instead of calling Claude again.
        """
        if desired_columns is None:
            # Default fallback list if no custom columns are passed
            desired_columns = [
                "symbol",
                "description",
                "shares",
                "avg_cost_basis",
                "current_price",
                "market_value",
                "daily_pnl",
                "daily_pnl_pct",
                "daily_pnl_session",  # Date the daily P/L covers (pre-market: the previous trading day)
                "last_close",  # Close before that session; previous portfolio value = sum(last_close x shares)
                "unrealized_pnl",
                "unrealized_pnl_pct",
                "recommendation",
                "total_cost_basis",
                "rsi",
                "recommendation_rationale",
                "natr",
                "annualized_vol",
                "currency",
                "account_name",
                "ma_status",
                "sentiment",
                "catalysts",
                "sector",
            ]

        holdings = self.full_data
        if symbols is not None and not holdings.empty:
            holdings = holdings[holdings["symbol"].isin(symbols)]

        holdings_with_metrics = self.add_metrics_to_holdings(holdings)
        holdings_with_recommendations = self.add_recommendations(
            holdings_with_metrics, reuse_recent_evaluations=reuse_recent_evaluations
        )
        # Claude-authored (2026-10-14): email-only display fields, added after the LLM step so Claude never sees them
        holdings_for_digest = add_digest_fields(holdings_with_recommendations)

        # Safely filter columns (ignores any column name not present in the DataFrame)
        available_cols = [
            col
            for col in desired_columns
            if col in holdings_for_digest.columns
        ]
        filtered_df = holdings_for_digest[available_cols]

        return {
            "holdings": filtered_df.to_dict(orient="records"),
            "portfolio_beta": portfolio_beta(holdings_for_digest),
            # Claude-authored (2026-10-15): why recommendations are missing, e.g. an expired or out-of-credit key
            "api_key_issues": [self.claude_key_error.as_notice()] if self.claude_key_error else [],
        }
        
        


if __name__ == "__main__":
    fetcher = PortfolioFetcher(user_name="Andres")    

    # 2. Extract simplified DataFrame
    df_simple = fetcher.get_holdings_digest_payload()

    # 3. Export to CSVs
    fetcher.full_data.to_csv("questrade_positions_full.csv", index=False)
    df_simple["holdings"].to_csv("questrade_positions_simple.csv", index=False)

    print("================ SIMPLIFIED DATAFRAME ================")
    print(df_simple)
    print("======================================================")