import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import finnhub
from dotenv import load_dotenv
from line_profiler import profile
import numpy as np
import pandas as pd
import yfinance as yf

import constants


class DataFetcher:
    """Handles fetching, processing, and exporting stock market data 
    from Yahoo Finance and Finnhub APIs.
    """

    def __init__(
        self,
        project_root: Optional[Union[str, Path]] = None,
        env_path: Optional[Union[str, Path]] = None,
    ):
        self.project_root = (
            Path(project_root).resolve()
            if project_root
            else self.__resolve_project_root()
        )
        self.env_path = (
            Path(env_path).resolve()
            if env_path
            else self.project_root / constants.DEFAULT_ENV_FILENAME
        )
        
        self.finnhub_client = self.__init_finnhub_client()
        self.all_tickers = self.__load_ticker_df()["ticker"].dropna().tolist()

    # ==========================================
    # INITIALIZATION & PATH HELPERS
    # ==========================================

    @staticmethod
    def __resolve_project_root() -> Path:
        """Determines project root directory based on current file location."""
        try:
            script_dir = Path(__file__).resolve().parent
        except NameError:
            script_dir = Path.cwd()

        return script_dir.parent if script_dir.name == "src" else script_dir

    def __init_finnhub_client(self) -> finnhub.Client:
        """Loads environment variables and initializes the Finnhub API client."""
        load_dotenv(dotenv_path=self.env_path)
        api_key = os.getenv(constants.ENV_KEY_FINNHUB)

        if not api_key:
            raise ValueError(
                f"'{constants.ENV_KEY_FINNHUB}' not found in environment or at '{self.env_path}'."
            )

        return finnhub.Client(api_key=api_key)

    def __get_latest_ticker_file(self) -> Path:
        """Finds the ticker CSV file with the most recent date prefix."""
        ticker_dir = self.project_root / "data" / "ticker_data"

        if not ticker_dir.exists():
            raise FileNotFoundError(f"Data directory '{ticker_dir}' does not exist.")

        matching_files = list(ticker_dir.glob("*_ticker_data.csv"))
        if not matching_files:
            raise FileNotFoundError(f"No matching files found in '{ticker_dir}'.")

        dated_files = []
        for file in matching_files:
            date_part = file.name.split("_")[0]
            try:
                file_date = datetime.strptime(date_part, "%d-%m-%y")
                dated_files.append((file_date, file))
            except ValueError:
                continue

        if not dated_files:
            raise FileNotFoundError(f"No validly dated CSV files in '{ticker_dir}'.")

        dated_files.sort(key=lambda x: x[0])
        return dated_files[-1][1]

    def __load_ticker_df(self) -> pd.DataFrame:
        """Loads the most recent ticker DataFrame from the ticker directory."""
        latest_file = self.__get_latest_ticker_file()
        print(f"Loading most recent ticker file: {latest_file.name}")
        return pd.read_csv(latest_file)

    # ==========================================
    # DATA RETRIEVAL METHODS
    # ==========================================

    from typing import Dict, List, Union, Any

    def fetch_yahoo_data(self, symbol: str, fetch_news: bool = False) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """Fetches key metrics (dict) or news articles (list of dicts) directly for a ticker from Yahoo Finance."""
        t_start = time.perf_counter()
        ticker_obj = yf.Ticker(symbol)

        def _safe_get(key: str, attribute_name: str, is_callable: bool = False, *args, **kwargs):
            try:
                attr = getattr(ticker_obj, attribute_name)
                res = attr(*args, **kwargs) if is_callable else attr

                if isinstance(res, (pd.DataFrame, pd.Series)):
                    return res.to_dict()

                if hasattr(res, "__dict__") and not isinstance(
                    res, (dict, list, tuple, str, int, float, bool)
                ):
                    out = {}
                    for prop in dir(res):
                        if not prop.startswith("_"):
                            val = getattr(res, prop)
                            if callable(val):
                                continue
                            out[prop] = val.to_dict() if isinstance(val, (pd.DataFrame, pd.Series)) else val
                    return out

                if hasattr(res, "keys") and not isinstance(res, dict):
                    try:
                        return dict(res)
                    except Exception:
                        pass

                return res
            except Exception as e:
                return {"error": f"Failed to fetch {key}: {str(e)}"}

        if fetch_news:
            news_res = _safe_get("news", "news")
            return news_res if isinstance(news_res, list) else [news_res]

        info_res = _safe_get("info", "info") #line takes 2000 milliseconds to run on avg.
        if isinstance(info_res, dict):
            info_clean = info_res.copy()
            info_clean.pop("companyOfficers", None)  # type: ignore
            return info_clean

        return info_res if isinstance(info_res, dict) else {"error": str(info_res)}

    def fetch_finnhub_data(self, symbol: str, fetch_news: bool = False) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """Fetches news (list of dicts) or profile/quote/financials (dict) from Finnhub."""
        def _safe_api_call(call_name: str, func, *args, **kwargs):
            try:
                res = func(*args, **kwargs)
                time.sleep(constants.FINNHUB_RATE_LIMIT_SLEEP)
                return res
            except Exception as e:
                return {"error": f"Failed to fetch {call_name}: {str(e)}"}

        if fetch_news:
            now = datetime.now()
            news_from_date = (
                now - timedelta(days=constants.COMPANY_NEWS_LOOKBACK_DAYS)
            ).strftime("%Y-%m-%d")
            today_date = now.strftime("%Y-%m-%d")

            news_res = _safe_api_call(
                "company_news",
                self.finnhub_client.company_news,
                symbol=symbol,
                _from=news_from_date,
                to=today_date,
            )
            return news_res if isinstance(news_res, list) else [news_res]

        finnhub_data: Dict[str, Any] = {}

        finnhub_data["profile"] = _safe_api_call(
            "company_profile2", self.finnhub_client.company_profile2, symbol=symbol
        )
        finnhub_data["quote"] = _safe_api_call(
            "quote", self.finnhub_client.quote, symbol
        )

        full_financials = _safe_api_call(
            "company_basic_financials",
            self.finnhub_client.company_basic_financials,
            symbol=symbol,
            metric="all",
        )
        finnhub_data["basic_financials"] = (
            full_financials.get("metric")
            if isinstance(full_financials, dict) and "metric" in full_financials
            else full_financials
        )

        return finnhub_data
    
    def __get_metric_with_alert(self, data: Dict[str, Any], key: str, symbol: str, default: Any = False) -> Any:
        if key not in data:
            print(f"Alert: Field '{key}' not found for ticker '{symbol}'.")
            return default
        return data[key]
    @profile
    def yahoo_filter(self, tickers: Optional[List[str]] = None) -> List[str]:
        """Filters tickers based on basic Yahoo parameters."""
        input_tickers = tickers if tickers is not None else self.all_tickers
        filtered_yahoo_tickers = []            
        
        for symbol in input_tickers:
            print(f"Fetching Yahoo data for {symbol}...")
            yahoo_metrics = self.fetch_yahoo_data(symbol=symbol, fetch_news=False)
            if not isinstance(yahoo_metrics, dict):
                print(f"Alert: Yahoo data for '{symbol}' is not a dictionary. Skipping.")
                continue
            
            # Sample Basic Filters:
            if not (market_cap := self.__get_metric_with_alert(yahoo_metrics, "marketCap", symbol)): continue
            if not market_cap >= 10_000: continue
            
            if not (regularMarketPreviousClose := self.__get_metric_with_alert(yahoo_metrics, "regularMarketPreviousClose", symbol)): continue
            if not regularMarketPreviousClose < 100: continue

            filtered_yahoo_tickers.append(symbol)
            print(f"✓ {symbol} passed Yahoo filters: Market Cap = {market_cap}, Market Close = {regularMarketPreviousClose}")
            
        return filtered_yahoo_tickers
    
    def finnhub_filter(self, tickers: Optional[List[str]] = None) -> List[str]:
        """Filters tickers based on Finnhub fundamental data."""
        input_tickers = tickers if tickers is not None else self.all_tickers
        filtered_finnhub_tickers = []            
        
        for symbol in input_tickers:
            print(f"Fetching Finnhub data for {symbol}...")
            finnhub_data = self.fetch_finnhub_data(symbol, fetch_news=False)
            if not isinstance(finnhub_data, dict):
                print(f"Alert: Finnhub data for '{symbol}' is not a dictionary. Skipping.")
                continue

            # Safely navigate nested keys matching Finnhub JSON payload
            profile = finnhub_data.get("profile", {})
            quote = finnhub_data.get("quote", {})
            basic_fin = finnhub_data.get("basic_financials", {})

            # Sample Basic Filters:
            # 1. Market Cap > $10 Billion (Finnhub reports market Capitalization in millions)
            # 2. Net Profit Margin (TTM) > 15%
            # 3. P/E TTM <= 25
            # 4. Revenue Growth (TTM YoY) > 5%
            
            if not (mcap_mil := self.__get_metric_with_alert(profile, "marketCapitalization", symbol)): continue
            if not mcap_mil >= 1_000: continue  # Market Cap in Millions USD
            
            if not (total_shares := self.__get_metric_with_alert(quote, "t", symbol)): continue
            if not total_shares > 100_000: continue  # Total Shares Outstanding
            
            if not (net_margin := self.__get_metric_with_alert(basic_fin, "netProfitMarginTTM", symbol)): continue
            if not net_margin > 1: continue
            
            if not (pe_ttm := self.__get_metric_with_alert(basic_fin, "peTTM", symbol, float("inf"))): continue
            if not (0 < pe_ttm <= 90): continue
            
            if not (rev_growth := self.__get_metric_with_alert(basic_fin, "revenueGrowthTTMYoy", symbol)): continue
            if not rev_growth > -30: continue

            filtered_finnhub_tickers.append(symbol)
            print(f"✓ {symbol} passed Finnhub filters: Market Cap = {mcap_mil}M, Total Shares = {total_shares}, Net Margin = {net_margin}%, P/E TTM = {pe_ttm}, Revenue Growth = {rev_growth}%")
            
        print(f"Filtered final tickers: {filtered_finnhub_tickers}")
        return filtered_finnhub_tickers
    
    def filter_all(self, full_tickers: Optional[List[str]] = None) -> List[str]:
        """Apply cascading filters across available data sources."""
        input_tickers = full_tickers if full_tickers is not None else self.all_tickers
        
        print("Starting to filter data based on Yahoo data")
        yahoo_tickers = self.yahoo_filter(tickers=input_tickers)
        
        print("Starting to filter data based on Finnhub data")
        finnhub_tickers = self.finnhub_filter(tickers=yahoo_tickers)
        
        print(f"Output tickers are: {finnhub_tickers}")
        return finnhub_tickers
        
    def fetch_all_news(self, tickers: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        """Fetches Yahoo and Finnhub news data for a list of tickers, normalizes and sorts
        articles chronologically, and returns a dictionary of DataFrames keyed by symbol.
        """
        if tickers is None:
            tickers = self.all_tickers

        news_data: Dict[str, pd.DataFrame] = {}

        dtype = [
            ("source", "U10"),
            ("timestamp", "f8"),
            ("date_str", "U30"),
            ("summary", "O"),
        ]

        for symbol in tickers:
            print(f"Fetching yahoo news for {symbol}...")
            yahoo_news = self.fetch_yahoo_data(symbol, fetch_news=True)
            print(f"Fetching finnhub news for {symbol}...")
            finnhub_news = self.fetch_finnhub_data(symbol, fetch_news=True)

            raw_records = []

            # 1. Parse Yahoo News
            if isinstance(yahoo_news, list):
                for item in yahoo_news:
                    if isinstance(item, dict):
                        content = item.get("content", {})
                        raw_date = content.get("pubDate") or content.get("displayTime")

                        if raw_date:
                            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                        else:
                            dt = datetime.min.replace(tzinfo=timezone.utc)

                        summary = content.get("summary", "").strip()
                        formatted_date = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                        raw_records.append(
                            ("Yahoo", dt.timestamp(), formatted_date, summary)
                        )

            # 2. Parse Finnhub News
            if isinstance(finnhub_news, list):
                for item in finnhub_news:
                    if isinstance(item, dict):
                        raw_ts = item.get("datetime")

                        if raw_ts:
                            dt = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
                        else:
                            dt = datetime.min.replace(tzinfo=timezone.utc)

                        summary = item.get("summary", "").strip()
                        formatted_date = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                        raw_records.append(
                            ("Finnhub", dt.timestamp(), formatted_date, summary)
                        )

            # 3. Create DataFrame (Sorted Newest to Oldest)
            if raw_records:
                news_array = np.array(raw_records, dtype=dtype)
                sorted_indices = np.argsort(news_array["timestamp"])[::-1]
                sorted_news = news_array[sorted_indices]
                df = pd.DataFrame(sorted_news)
                print(f"✓ Successfully fetched and sorted news for {symbol}. Total articles: {len(df)}")
            else:
                df = pd.DataFrame(columns=["source", "timestamp", "date_str", "summary"])

            news_data[symbol] = df

        return news_data       
    
    def fetch_all(self, tickers: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        """Fetches Yahoo and Finnhub data for a list of tickers (or loads from CSV if None)."""
        if tickers is None:
            tickers = self.all_tickers

        market_data: Dict[str, Dict[str, Any]] = {}

        for symbol in tickers:
            print(f"Fetching yahoo data for {symbol}...")
            yahoo_data = self.fetch_yahoo_data(symbol)
            print(f"Fetching finnhub data for {symbol}...")
            finnhub_data = self.fetch_finnhub_data(symbol, fetch_news=False)

            market_data[symbol] = {
                "yahoo": yahoo_data,
                "finnhub": finnhub_data,
            }

        return market_data

    # ==========================================
    # DATA SANITIZATION & EXPORT
    # ==========================================

    @staticmethod
    def sanitize_for_json(obj: Any) -> Any:
        """Recursively converts Pandas objects, datetimes, and custom objects into JSON-safe types."""
        if isinstance(obj, (pd.DataFrame, pd.Series)):
            return DataFetcher.sanitize_for_json(obj.to_dict())

        if hasattr(obj, "keys") and not isinstance(obj, dict):
            try:
                return DataFetcher.sanitize_for_json({k: obj[k] for k in obj.keys()})
            except Exception:
                return str(obj)

        if isinstance(obj, dict):
            sanitized_dict = {}
            for key, value in obj.items():
                if isinstance(key, (pd.Timestamp, pd.Timedelta, datetime)):
                    str_key = key.isoformat()
                elif not isinstance(key, (str, int, float, bool, type(None))):
                    str_key = str(key)
                else:
                    str_key = key
                sanitized_dict[str_key] = DataFetcher.sanitize_for_json(value)
            return sanitized_dict

        if isinstance(obj, (list, tuple, set)):
            return [DataFetcher.sanitize_for_json(item) for item in obj]

        if isinstance(obj, (pd.Timestamp, pd.Timedelta, datetime)):
            return obj.isoformat()

        if pd.isna(obj):
            return None

        if not isinstance(obj, (str, int, float, bool, type(None))):
            if hasattr(obj, "__dict__"):
                return DataFetcher.sanitize_for_json(obj.__dict__)
            return str(obj)

        return obj

    def export_ticker_files(
        self,
        market_data: Dict[str, Dict[str, Any]],
        symbol: str,
        output_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        """Exports Yahoo and Finnhub data for a given ticker into separate formatted JSON files."""
        ticker_data = market_data.get(symbol)
        if not ticker_data or "yahoo" not in ticker_data or "finnhub" not in ticker_data:
            print(f"Data for '{symbol}' not found or improperly formatted.")
            return

        out_path = Path(output_dir) if output_dir else self.project_root
        out_path.mkdir(parents=True, exist_ok=True)

        yahoo_clean = self.sanitize_for_json(ticker_data["yahoo"])
        finnhub_clean = self.sanitize_for_json(ticker_data["finnhub"])

        yahoo_filename = out_path / f"{symbol.lower()}_yahoo_data.json"
        finnhub_filename = out_path / f"{symbol.lower()}_finnhub_data.json"

        with open(yahoo_filename, "w", encoding="utf-8") as f:
            json.dump(yahoo_clean, f, indent=4, ensure_ascii=False)

        with open(finnhub_filename, "w", encoding="utf-8") as f:
            json.dump(finnhub_clean, f, indent=4, ensure_ascii=False)

        print(f"Successfully exported '{yahoo_filename.name}' and '{finnhub_filename.name}'!")