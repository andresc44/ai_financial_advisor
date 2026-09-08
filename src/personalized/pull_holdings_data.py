# File to pull portfolio data using snaptrade api and convert to pandas dataframe for analysis and reporting.
import math
import os
import pandas as pd
import yfinance as yf
import io
import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from dotenv import load_dotenv
from snaptrade_client import SnapTrade, SnapTradeAuth
from user_profiles import USER_PROFILES
from add_metrics import add_metrics_to_holdings
from recommendation import add_recommendations


class PortfolioFetcher:
    add_metrics_to_holdings = add_metrics_to_holdings #method to append online data to the holdings dataframe
    add_recommendations = add_recommendations #method to evaluate hold positions and add recommendations to the holdings dataframe
    
    def __init__(self, user_name: str = "Andres", env_path: str = ".env"):
        print("Searching for user snaptrade credentials in .env file...")
        load_dotenv(dotenv_path=env_path, override=True)

        user_profile = USER_PROFILES.get(user_name)
        if user_profile is None:
            raise ValueError(f"User '{user_name}' not found in USER_PROFILES.")

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

    def _fetch_yfinance_prev_closes(self, symbols: list) -> dict:
        prev_closes = {}
        if not symbols:
            return prev_closes

        sym_map = {
            s: s.replace(":", "-")
            for s in set(symbols)
            if isinstance(s, str) and s
        }
        formatted_symbols = list(sym_map.values())

        print(
            f"Batch fetching previous closes for {len(formatted_symbols)}"
            " symbols..."
        )

        try:
            data = yf.download(
                tickers=formatted_symbols,
                period="5d",
                interval="1d",
                progress=False,
                threads=True,
            )

            close_df = data["Close"] if "Close" in data else data

            for orig_sym, sym in sym_map.items():
                if (
                    sym in close_df.columns
                    if len(formatted_symbols) > 1
                    else True
                ):
                    series = (
                        close_df[sym]
                        if len(formatted_symbols) > 1
                        else close_df
                    ).dropna()

                    if len(series) >= 2:
                        prev_closes[orig_sym] = float(series.iloc[-2])
                    elif len(series) == 1:
                        prev_closes[orig_sym] = float(series.iloc[-1])

        except Exception as e:
            print(f"[ERROR] Batch download failed: {e}")

        return prev_closes

    def calculate_daily_pnl(self, df: pd.DataFrame = None) -> pd.DataFrame:
        """Fetches last close prices from yfinance and calculates daily profit/loss."""
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

        prev_closes = self._fetch_yfinance_prev_closes(symbols)

        df["last_close"] = df["symbol"].map(prev_closes)
        df["last_close"] = df["last_close"].fillna(df["current_price"])

        df["daily_pnl"] = (df["current_price"] - df["last_close"]) * df["shares"]
        df["daily_pnl_pct"] = df.apply(
            lambda r: (
                ((r["current_price"] - r["last_close"]) / r["last_close"] * 100)
                if r["last_close"] > 0
                else 0.0
            ),
            axis=1,
        )

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
                    if len(formatted_symbols) == 1:
                        ticker_df = hist_data.copy()
                    else:
                        ticker_df = pd.DataFrame(
                            {
                                "High": hist_data["High"][sym],
                                "Low": hist_data["Low"][sym],
                                "Close": hist_data["Close"][sym],
                            }
                        ).dropna()

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
        print("os.path.exists(creds_path):", os.path.exists(creds_path))
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
    
    
    def get_holdings_digest_payload(self, desired_columns: list[str] = None) -> dict:
        """Return a dictionary payload containing the holdings data filtered to desired columns."""
        if desired_columns is None:
            # Default fallback list if no custom columns are passed
            desired_columns = [
                "symbol",
                "shares",
                "avg_cost_basis",
                "current_price",
                "market_value",
                "unrealized_pnl",
                "unrealized_pnl_pct",
                "recommendation",
            ]

        holdings_with_metrics = self.add_metrics_to_holdings(self.full_data)
        holdings_with_recommendations = self.add_recommendations(
            holdings_with_metrics
        )

        # Safely filter columns (ignores any column name not present in the DataFrame)
        available_cols = [
            col
            for col in desired_columns
            if col in holdings_with_recommendations.columns
        ]
        filtered_df = holdings_with_recommendations[available_cols]

        return {"holdings": filtered_df.to_dict(orient="records")}
        
        


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