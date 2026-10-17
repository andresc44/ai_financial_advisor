# File to grab data from Yahoo Finance and Finnhub APIs, filter tickers based on fundamental data, and export the results to JSON files.
import copy
import json
import os
import time
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import requests

import finnhub
from line_profiler import profile
import numpy as np
import pandas as pd
import yfinance as yf

import sys
from pathlib import Path
from dotenv import load_dotenv

from concurrent.futures import ThreadPoolExecutor

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
env_path = project_root / ".env"
load_dotenv(dotenv_path=env_path, override=True)

from src.universal.recommendations.yahoo_filter_helper import filter_yahoo_tickers
from src.universal.recommendations.finnhub_filter_helper import filter_finnhub_tickers
from src.universal.recommendations.twelveData_filter_helper import filter_twelvedata_tickers, fetch_twelvedata_context_data
from src.parameters import params_dict
from src.universal.recommendations.data_source_guard import (
    DataSourceIssues,
    GuardedFinnhubClient,
    twelvedata_request,
    yahoo_symbol,
)
from src.universal.recommendations import local_indicators

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
            else self.project_root / ".env"
        )
        
        # Claude-authored (2026-10-16): problems with the shared Finnhub / Twelve Data keys found this run
        # (see data_source_guard.py); read by the universal content to alert the admins
        self.data_source_issues = DataSourceIssues()
        self._twelvedata_state: dict = {}
        self.finnhub_client = self.__init_finnhub_client()
        self.all_tickers = self.__load_ticker_df()["ticker"].dropna().tolist()
        self.twelvedata_api_key = os.getenv("TWELVEDATA_API_KEY", "")
        self.session = requests.Session()
        fundamental_days_back = params_dict["FUNDAMENTAL_MONTHS_LOOKBACK"]*31
        self.cutoff_date = (datetime.now() - timedelta(days=fundamental_days_back)).strftime("%Y-%m-%d")

    # ==========================================
    # INITIALIZATION & PATH HELPERS
    # ==========================================

    @staticmethod
    def __resolve_project_root() -> Path:
        """Determines project root directory based on current file location."""

        return Path(__file__).resolve().parents[3]

    def __init_finnhub_client(self) -> finnhub.Client:
        """Loads environment variables and initializes the Finnhub API client."""
        api_key = os.getenv("FINNHUB_API_KEY")

        if not api_key:
            raise ValueError(
                f"'FINNHUB_API_KEY' not found in environment or at '{self.env_path}'."
            )

        # Claude-authored (2026-10-16): rate limits retried, a rejected key reported once (data_source_guard.py)
        return GuardedFinnhubClient(finnhub.Client(api_key=api_key), self.data_source_issues)

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
                file_date = datetime.strptime(date_part, "%d-%m-%y-%p")
                dated_files.append((file_date, file))
            except ValueError:
                continue

        if not dated_files:
            raise FileNotFoundError(f"No validly dated CSV files in '{ticker_dir}'.")

        # Claude-authored (2026-10-16): newest write breaks a same-date tie (files are now also named by their filter
        # settings, so two can share a date when CLEAR_EXISTING_DATA is off)
        dated_files.sort(key=lambda x: (x[0], x[1].stat().st_mtime))
        return dated_files[-1][1]

    def __load_ticker_df(self) -> pd.DataFrame:
        """Loads the most recent ticker DataFrame from the ticker directory."""
        latest_file = self.__get_latest_ticker_file()
        self.ticker_file = latest_file  # Claude-authored (2026-10-17): for the screening counts
        print(f"Loading most recent ticker file: {latest_file.name}")
        tickers = pd.read_csv(latest_file)
        # Claude-authored (2026-10-17): the screener's BRK/B becomes Yahoo's BRK-B; Finnhub calls convert it again
        tickers["ticker"] = tickers["ticker"].map(yahoo_symbol)
        return tickers

    # ==========================================
    # DATA RETRIEVAL METHODS
    # ==========================================


    def fetch_yahoo_data(self, symbol: str, fetch_news: bool = False) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """Fetches key metrics (dict) or news articles (list of dicts) directly for a ticker from Yahoo Finance."""
        # Claude-authored (2026-10-16): the Yahoo filter stage already fetched each candidate's info this run, so it's
        # reused when the survivors' full data is gathered (fetch_all) instead of downloading it twice
        info_cache = self.__dict__.setdefault("_yahoo_info_cache", {})
        if not fetch_news and symbol in info_cache:
            return dict(info_cache[symbol])
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
            if "error" not in info_clean:
                info_cache[symbol] = info_clean
            return dict(info_clean)

        return info_res if isinstance(info_res, dict) else {"error": str(info_res)}

    def fetch_finnhub_data(self, symbol: str, fetch_news: bool = False) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        """Fetches news (list of dicts) or profile/quote/financials (dict) from Finnhub."""
        def _safe_api_call(call_name: str, func, *args, **kwargs):
            try:
                res = func(*args, **kwargs)
                time.sleep(params_dict["FINNHUB_RATE_LIMIT_SLEEP"])
                return res
            except Exception as e:
                return {"error": f"Failed to fetch {call_name}: {str(e)}"}

        if fetch_news:
            now = datetime.now()
            news_from_date = (
                now - timedelta(days=params_dict["COMPANY_NEWS_LOOKBACK_DAYS"])
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

        # Claude-authored (2026-10-16): reuse what the Finnhub filter stage fetched this run (4 calls per ticker, under
        # the free plan's 60 calls/minute) when the survivors' full data is gathered
        finnhub_cache = self.__dict__.setdefault("_finnhub_cache", {})
        if symbol in finnhub_cache:
            return copy.deepcopy(finnhub_cache[symbol])

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
        
        # Define dynamic date range for future corporate events 
        today = datetime.now()
        from_date = today.strftime("%Y-%m-%d")
        to_date = (today + timedelta(days=params_dict["EARNINGS_FUTURE_LOOKAHEAD_DAYS"])).strftime("%Y-%m-%d")

        # Fetch upcoming earnings calendar data
        finnhub_data["earnings_calendar"] = _safe_api_call(
            "earnings_calendar",
            self.finnhub_client.earnings_calendar,
            _from=from_date,
            to=to_date,
            symbol=symbol,
        )

        if not any(isinstance(section, dict) and "error" in section for section in finnhub_data.values()):
            finnhub_cache[symbol] = copy.deepcopy(finnhub_data)  # A failed call is retried if the ticker comes back
        return finnhub_data
    

    def fetch_twelvedata_batch_data(
        self, 
        symbols: List[str], 
        indicators: List[tuple[str, dict] | str], 
        interval: str = "1day"
    ) -> Dict[str, Any]:
        if not symbols or not indicators:
            print("[DEBUG] fetch_twelvedata_batch_data called with empty symbols or indicators.")
            return {}

        symbols_str = ",".join(symbols)
        batch_results: Dict[str, Any] = {}
        # Claude-authored (2026-10-16): with TECHNICAL_INDICATOR_SOURCE "yahoo" (the default), every indicator
        # local_indicators.py implements is computed from Yahoo's daily bars (one download for all symbols, no credits);
        # only the rest go to Twelve Data below. Same result format either way.
        compute_locally = params_dict.get("TECHNICAL_INDICATOR_SOURCE", "yahoo") == "yahoo"

        for item in indicators:
            indicator_name, extra_params = item if isinstance(item, tuple) else (item, {})
            url = f"https://api.twelvedata.com/{indicator_name.lower()}"

            params = {
                "symbol": symbols_str,
                "interval": interval,
                "apikey": self.twelvedata_api_key,
                **extra_params
            }

            # Generate unique key per parameter set (e.g., 'rsi' vs 'rsi_9')
            param_suffix = f"_{extra_params.get('time_period')}" if "time_period" in extra_params else ""
            indicator_key = f"{indicator_name}{param_suffix}"

            if compute_locally and local_indicators.supports(indicator_name, interval, extra_params):
                bars = self._daily_bars(symbols)
                for symbol in symbols:
                    values = local_indicators.compute(
                        indicator_name, interval, extra_params, bars.get(symbol), bars.get(local_indicators.MARKET_SYMBOL)
                    )
                    if values:
                        batch_results.setdefault(symbol, {})[indicator_key] = {"values": values}
                continue

            try:
                # Claude-authored (2026-10-16): rate limits waited out, a used-up allowance or rejected key reported
                # once and not retried for the rest of the run (data_source_guard.py)
                data = twelvedata_request(
                    self.session.get, url, params, self.data_source_issues, self._twelvedata_state
                )
                if data is None:
                    continue

                if len(symbols) == 1:
                    batch_results.setdefault(symbols[0], {})[indicator_key] = data
                else:
                    for symbol, payload in data.items():
                        batch_results.setdefault(symbol, {})[indicator_key] = payload

            except Exception as e:
                print(f"[DEBUG Exception] Failed fetching {indicator_key}: {e}")

        print(f"[DEBUG Batch Results Complete] Formatted keys: {list(batch_results.keys())}")
        return batch_results

    def _daily_bars(self, symbols: List[str]) -> Dict[str, Any]:
        """
        Claude-authored (2026-10-16): unadjusted daily bars for `symbols` and SPY (for beta), downloaded once per run in
        a single Yahoo request and reused by every indicator. A failed download is reported as a data-source issue and
        not retried this run; those symbols then have no indicators, which the completeness check also flags.
        """
        cache = self.__dict__.setdefault("_daily_bars_cache", {})
        missing = sorted((set(symbols) | {local_indicators.MARKET_SYMBOL}) - set(cache))
        if missing:
            try:
                cache.update(local_indicators.download_daily_bars(missing))
            except Exception as e:
                print(f"[WARNING] Yahoo daily prices for technical indicators failed: {e}")
                issues = self.__dict__.get("data_source_issues")
                if issues is not None:
                    issues.add("Yahoo prices", "failed",
                               f"Daily prices for the technical indicators couldn't be downloaded ({type(e).__name__}).")
            for symbol in missing:
                cache.setdefault(symbol, None)
        return cache

    def __get_metric_with_alert(self, data: Dict[str, Any], key: str, symbol: str, default: Any = False) -> Any:
        if key not in data:
            print(f"Alert: Field '{key}' not found for ticker '{symbol}'.")
            return default
        return data[key]
    @profile
    def yahoo_filter(self, tickers: Optional[List[str]] = None) -> List[str]:
        """Delegates filtering logic to the standalone helper function."""
        return filter_yahoo_tickers(self, tickers=tickers)

    def finnhub_filter(self, tickers: Optional[List[str]] = None) -> List[str]:
        """Filters tickers based on Finnhub fundamental data."""
        return filter_finnhub_tickers(self, tickers=tickers)

    def twelvedata_filter(self, tickers: Optional[List[str]] = None) -> List[str]:
        """Delegates technical indicator filtering to twelvedata_helpers."""
        return filter_twelvedata_tickers(self, tickers=tickers)
    
    def filter_all(self, full_tickers: Optional[List[str]] = None) -> List[str]:
        """
        Apply cascading filters across available data sources.
        Claude-authored (2026-10-17): self.stage_counts records how many tickers went in and how many each stage
        kept ({"screener", "yahoo", "finnhub", "technical"}), for the digest's screening counts.
        """
        input_tickers = full_tickers if full_tickers is not None else self.all_tickers
        
        print("Starting to filter data based on Yahoo data")
        yahoo_tickers = self.yahoo_filter(tickers=input_tickers)
        
        print("Starting to filter data based on Finnhub data")
        finnhub_tickers = self.finnhub_filter(tickers=yahoo_tickers)
        
        print("Starting to filter data based on Twelve Data technical indicators")
        twelvedata_tickers = self.twelvedata_filter(tickers=finnhub_tickers)
        
        print(f"Output tickers are: {twelvedata_tickers}")
        self.stage_counts = {"screener": len(input_tickers), "yahoo": len(yahoo_tickers),
                             "finnhub": len(finnhub_tickers), "technical": len(twelvedata_tickers)}
        print(f"Tickers kept by each stage: {self.stage_counts}")
        return twelvedata_tickers
        
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
        """Fetches Yahoo, Finnhub, and Twelve Data context for a list of tickers."""
        if tickers is None:
            tickers = self.all_tickers

        # 1. Batch fetch Twelve Data context upfront for all tickers to save API latency
        twelvedata_context = fetch_twelvedata_context_data(self, tickers=tickers)

        market_data: Dict[str, Dict[str, Any]] = {}

        # 2. Iterate and assemble combined ticker datasets
        for symbol in tickers:
            print(f"Fetching yahoo data for {symbol}...")
            yahoo_data = self.fetch_yahoo_data(symbol)
            print(f"Fetching finnhub data for {symbol}...")
            finnhub_data = self.fetch_finnhub_data(symbol, fetch_news=False)

            market_data[symbol] = {
                "yahoo": yahoo_data,
                "finnhub": finnhub_data,
                "twelvedata": twelvedata_context.get(symbol, {}),
            }

        return market_data
    
    def fetch_series_data(
        self,
        tickers: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Fetches series data across Finnhub endpoints using ALL CAPS parameter keys.

        Falls back to src.parameters.params_dict if no custom params_dict is passed.
        """
        if tickers is None:
            tickers = self.all_tickers

        earnings_cfg = params_dict.get("EARNINGS_SURPRISE", {})
        insider_cfg = params_dict.get("INSIDER_SENTIMENT", {})
        fin_reported_cfg = params_dict.get("FINANCIALS_REPORTED", {})
        basic_fin_cfg = params_dict.get("COMPANY_BASIC_FINANCIALS", {})

        series_payload: Dict[str, Dict[str, Any]] = {}

        for symbol in tickers:
            ticker_data: Dict[str, Any] = {}

            # 1. Recommendation Trends
            try:
                ticker_data["recommendation_trends"] = (
                    self.finnhub_client.recommendation_trends(symbol=symbol)
                )
                print(f"✓ Successfully fetched recommendation trends for {symbol}.")
            except Exception as e:
                ticker_data["recommendation_trends"] = {"error": str(e)}

            # 2. Earnings Surprise
            try:
                kwargs = {}
                if "LIMIT" in earnings_cfg:
                    kwargs["limit"] = earnings_cfg["LIMIT"]

                ticker_data["earnings_surprise"] = (
                    self.finnhub_client.company_earnings(symbol=symbol, **kwargs)
                )
                print(f"✓ Successfully fetched earnings surprise data for {symbol}.")
            except Exception as e:
                ticker_data["earnings_surprise"] = {"error": str(e)}

            # 3. Insider Sentiment
            try:
                kwargs = {}
                if "DAYS_BACK" in insider_cfg:
                    today = date.today()
                    from_date = today - timedelta(days=insider_cfg["DAYS_BACK"])

                    kwargs["to"] = today.strftime("%Y-%m-%d")
                    kwargs["_from"] = from_date.strftime("%Y-%m-%d")

                raw_sentiment = self.finnhub_client.stock_insider_sentiment(
                    symbol=symbol, **kwargs
                )
                
                if isinstance(raw_sentiment, dict) and isinstance(raw_sentiment.get("data"), list):
                    raw_sentiment["data"].sort(
                        key=lambda x: (x.get("year", 0), x.get("month", 0)), 
                        reverse=True
                    )
                
                ticker_data["insider_sentiment"] = raw_sentiment
                
                print(f"✓ Successfully fetched insider sentiment data for {symbol}.")
                
            except Exception as e:
                ticker_data["insider_sentiment"] = {"error": str(e)}

            # # 4. Financials Reported UNNECESSARY SEC-filing data, waste of tokens
            # try:
            #     kwargs = {}
            #     if "FREQ" in fin_reported_cfg:
            #         kwargs["freq"] = fin_reported_cfg["FREQ"]
            #     if fin_reported_cfg.get("ID"):
            #         kwargs["id"] = fin_reported_cfg["ID"]
            #     if fin_reported_cfg.get("ACCESS_NUMBER"):
            #         kwargs["access_number"] = fin_reported_cfg["ACCESS_NUMBER"]

            #     # Fetch raw data from Finnhub API
            #     raw_response = self.finnhub_client.financials_reported(
            #         symbol=symbol, **kwargs
            #     )

            #     reports = raw_response.get("data", [])

            #     # Filter reports to only include those filed/reported in the current year or later
            #     valid_reports = [
            #         r for r in reports if r.get("year", 0) >= self.current_year
            #     ]

            #     # Sort descending by year and filedDate to guarantee the most recent report comes first
            #     valid_reports.sort(
            #         key=lambda x: (x.get("year", 0), x.get("filedDate", "")),
            #         reverse=True
            #     )

            #     # Reconstruct dictionary payload containing only the 1 most recent report
            #     ticker_data["financials_reported"] = {
            #         "cik": raw_response.get("cik", ""),
            #         "symbol": raw_response.get("symbol", symbol),
            #         "data": valid_reports[:1]  # Restrict array to exactly 1 report
            #     }

            #     print(f"✓ Successfully fetched most recent financials reported data for {symbol}.")
            # except Exception as e:
            #     ticker_data["financials_reported"] = {"error": str(e)}


            # 5. Company Basic Financials ('series' key only)
            try:
                kwargs = {}
                if "METRIC" in basic_fin_cfg:
                    kwargs["metric"] = basic_fin_cfg["METRIC"]

                # Fetch the raw data; Finnhub does not support date filtering kwargs for this endpoint
                raw_basic = self.finnhub_client.company_basic_financials(
                    symbol=symbol, **kwargs
                )
                    
                if isinstance(raw_basic, dict) and "series" in raw_basic:
                    series = raw_basic.get("series", {})
                    quarterly_raw = series.get("quarterly", {})
                    
                    filtered_quarterly = {}
                    
                    for metric_name, data_points in quarterly_raw.items():
                        # String comparison works perfectly for YYYY-MM-DD formats
                        filtered_points = [
                            dp for dp in data_points 
                            if dp.get("period", "") >= self.cutoff_date
                        ]
                        
                        # Only add the metric if there is data within the last 24 months
                        if filtered_points:
                            filtered_quarterly[metric_name] = filtered_points
                    
                    # Rebuild the series_financials dict with the filtered quarterly data
                    ticker_data["series_financials"] = {
                        "quarterly": filtered_quarterly,
                        # "annual": series.get("annual", {}) # Keeps annual data intact (remove if unneeded)
                    }
                else:
                    ticker_data["series_financials"] = {}
                        
            except Exception as e:
                ticker_data["series_financials"] = {"error": str(e)}

            series_payload[symbol] = ticker_data

        return series_payload

    def congregate_LLM_input_data(
        self,
        tickers: Optional[List[str]] = None,
        as_dict_records: bool = True,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Combines market data, Finnhub series data, and news into a unified dictionary."""
        if tickers is None:
            tickers = self.all_tickers

        print(f"Fetching market and news data for {len(tickers)} tickers...")
        market_data = self.fetch_all(tickers=tickers)
        print(f"✓ Successfully fetched market data for {len(market_data)} tickers.")

        news_data = self.fetch_all_news(tickers=tickers)
        print(f"✓ Successfully fetched news data for {len(news_data)} tickers.")

        print(f"Fetching Finnhub series data for {len(tickers)} tickers...")
        series_data = self.fetch_series_data(
            tickers=tickers
        )
        print(
            f"✓ Successfully fetched series data for {len(series_data)} tickers."
        )

        llm_payload: Dict[str, Dict[str, Any]] = {}

        for symbol in tickers:
            symbol_market = market_data.get(symbol, {})
            symbol_news_df = news_data.get(symbol, pd.DataFrame())
            symbol_series = series_data.get(symbol, {})

            if as_dict_records and isinstance(symbol_news_df, pd.DataFrame):
                news_payload = symbol_news_df.to_dict(orient="records")
            else:
                news_payload = symbol_news_df

            finnhub_payload = symbol_market.get("finnhub", {})
            if isinstance(finnhub_payload, dict):
                finnhub_payload.update(symbol_series)
                print(f"✓ Merged series data into Finnhub payload for {symbol}.")
            else:
                finnhub_payload = symbol_series

            llm_payload[symbol] = {
                "yahoo": symbol_market.get("yahoo", {}),
                "finnhub": finnhub_payload,
                "twelvedata": symbol_market.get("twelvedata", {}),
                "news": news_payload,
            }
            print(
                f"✓ Aggregated LLM payload for {symbol}: {len(news_payload)} news articles"
            )

        return llm_payload
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