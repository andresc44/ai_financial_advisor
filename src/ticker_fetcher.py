from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import pandas as pd
import requests

from constants import (
    INDUSTRIES,
    REGION_COUNTRY_MAP,
    SECTORS,
    VALID_REGIONS,
)


class Tickers:
    """Class to fetch, filter, and manage NASDAQ stock ticker datasets."""

    # Class Attributes / Constants
    INDUSTRIES = INDUSTRIES
    REGION_COUNTRY_MAP = REGION_COUNTRY_MAP
    SECTORS = SECTORS
    VALID_REGIONS = VALID_REGIONS

    _API_URL = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25000&download=true"
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
    }

    def __init__(self, output_dir: Path | str | None = None) -> None:
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            try:
                script_dir = Path(__file__).resolve().parent
            except NameError:
                script_dir = Path.cwd()

            project_root = script_dir.parent if script_dir.name == "src" else script_dir
            self.output_dir = project_root / "data" / "ticker_data"

    # --- Public Getter Methods ---

    def get_available_sectors(self) -> list[str]:
        """Returns all standard NASDAQ sectors available for filtering."""
        return sorted(set(self.SECTORS.values()))

    def get_available_regions(self, include_countries: bool = True) -> dict[str, list[str]]:
        """Returns valid geographical regions and optionally their mapped countries as a dictionary."""
        regions = sorted(self.VALID_REGIONS)
        if not include_countries:
            return {reg: [] for reg in regions}
        return {
            reg: sorted(list(self.REGION_COUNTRY_MAP.get(reg, [])))
            for reg in regions
        }

    def get_available_industries(self) -> list[str]:
        """Returns all unique NASDAQ industries available for filtering."""
        return sorted(self.INDUSTRIES)

    # --- Main Data Processing Method ---

    def fetch_ticker_data(
        self,
        exchanges: dict[str, bool] | None = None,
        mktcap_min: float | int | None = None,
        mktcap_max: float | int | None = None,
        volume_min: float | int | None = None,
        volume_max: float | int | None = None,
        lastsale_min: float | int | None = None,
        lastsale_max: float | int | None = None,
        region: str | None = None,
        country: str | None = None,
        sector: str | None = None,
        industry: str | None = None,
        clear_existing_data: bool = True,
    ) -> Path | None:
        if exchanges is None:
            exchanges = {"NASDAQ": True, "NYSE": True, "AMEX": True}

        clean_region = region.upper() if isinstance(region, str) else None
        valid_region = clean_region if clean_region in self.VALID_REGIONS else None
        clean_country = country.strip() if isinstance(country, str) and country.strip() else None
        clean_industry = industry.strip() if isinstance(industry, str) and industry.strip() else None

        sector_val = self._resolve_sector(sector)

        us_eastern_time = datetime.now(ZoneInfo("America/New_York"))
        date_str = us_eastern_time.strftime("%d-%m-%y")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        name_parts = [date_str]
        all_exchanges_selected = exchanges and all(exchanges.values())
        active_ex = [k.lower() for k, v in exchanges.items() if v]

        # if not all_exchanges_selected:
        #     if active_ex:
        #         name_parts.append("_".join(active_ex))
        #     else:
        #         name_parts.append("no_exchange")

        # # Mutually exclusive: Region takes priority over Country
        # if valid_region:
        #     name_parts.append(f"region_{valid_region.lower()}")
        # elif clean_country:
        #     country_slug = clean_country.lower().replace(" ", "_")
        #     name_parts.append(f"country_{country_slug}")

        # # Mutually exclusive: Sector takes priority over Industry
        # if sector_val:
        #     name_parts.append(f"sector_{sector_val.lower().replace(' ', '_')}")
        # elif clean_industry:
        #     ind_slug = (
        #         clean_industry.lower()
        #         .translate(str.maketrans("", "", ":/\\*?\"<>|"))
        #         .replace(" ", "_")
        #     )
        #     name_parts.append(f"ind_{ind_slug}")

        # if mktcap_min is not None or mktcap_max is not None:
        #     cap_parts = []
        #     if mktcap_min is not None:
        #         cap_parts.append(f"min{mktcap_min}")
        #     if mktcap_max is not None:
        #         cap_parts.append(f"max{mktcap_max}")
        #     name_parts.append(f"mktcap_{'_'.join(cap_parts)}")

        # if volume_min is not None or volume_max is not None:
        #     vol_parts = []
        #     if volume_min is not None:
        #         vol_parts.append(f"min{volume_min}")
        #     if volume_max is not None:
        #         vol_parts.append(f"max{volume_max}")
        #     name_parts.append(f"vol_{'_'.join(vol_parts)}")

        # if lastsale_min is not None or lastsale_max is not None:
        #     sale_parts = []
        #     if lastsale_min is not None:
        #         sale_parts.append(f"min{lastsale_min}")
        #     if lastsale_max is not None:
        #         sale_parts.append(f"max{lastsale_max}")
        #     name_parts.append(f"lastsale_{'_'.join(sale_parts)}")

        # if len(name_parts) == 1:
        #     name_parts.append("all")

        prefix = "_".join(name_parts)
        ticker_file_name = f"{prefix}_ticker_data.csv"
        ticker_file_path = self.output_dir / ticker_file_name

        if ticker_file_path.exists():
            print(f"Data file for today already exists: {ticker_file_name}")
            return ticker_file_path

        if clear_existing_data:
            self._clear_ticker_data_folder(self.output_dir)

        try:
            print("Fetching raw ticker dataset from NASDAQ API...")
            df_filtered = self._fetch_from_nasdaq()

            if "exchange" in df_filtered.columns:
                df_filtered = df_filtered[
                    df_filtered["exchange"].str.upper().isin([e.upper() for e in active_ex])
                ]

            # Region / Country filter (Mutually exclusive)
            if valid_region and "country" in df_filtered.columns:
                target_countries = self.REGION_COUNTRY_MAP.get(valid_region, set())
                df_filtered = df_filtered[
                    df_filtered["country"].str.title().isin(target_countries)
                ]
            elif clean_country and "country" in df_filtered.columns:
                df_filtered = df_filtered[
                    df_filtered["country"].str.strip().str.upper() == clean_country.upper()
                ]

            if mktcap_min is not None:
                df_filtered = df_filtered[df_filtered["marketCapNumeric"] >= mktcap_min]
            if mktcap_max is not None:
                df_filtered = df_filtered[df_filtered["marketCapNumeric"] <= mktcap_max]

            if volume_min is not None:
                df_filtered = df_filtered[df_filtered["volumeNumeric"] >= volume_min]
            if volume_max is not None:
                df_filtered = df_filtered[df_filtered["volumeNumeric"] <= volume_max]

            if lastsale_min is not None:
                df_filtered = df_filtered[df_filtered["lastSaleNumeric"] >= lastsale_min]
            if lastsale_max is not None:
                df_filtered = df_filtered[df_filtered["lastSaleNumeric"] <= lastsale_max]

            # Sector / Industry filter (Mutually exclusive)
            if sector_val and "sector" in df_filtered.columns:
                df_filtered = df_filtered[
                    df_filtered["sector"].str.strip().str.upper() == sector_val.upper()
                ]
            elif clean_industry and "industry" in df_filtered.columns:
                df_filtered = df_filtered[
                    df_filtered["industry"].str.contains(clean_industry, case=False, na=False)
                ]

            if "lastsale" in df_filtered.columns and "lastSale" not in df_filtered.columns:
                df_filtered = df_filtered.rename(columns={"lastsale": "lastSale"})

            column_mapping = {
                "symbol": "ticker",
                "name": "companyName",
                "lastSaleNumeric": "price",
                "volumeNumeric": "volume_M",
                "marketCapNumeric": "marketCap_M",
                "sector": "sector",
                "industry": "industry",
                "country": "country",
            }

            existing_cols = [c for c in column_mapping.keys() if c in df_filtered.columns]

            # Select target columns, rename, and export to CSV
            df_filtered[existing_cols].rename(columns=column_mapping).to_csv(
                ticker_file_path, index=False
            )
            print(f"Successfully saved filtered dataset ({len(df_filtered)} rows) to: {ticker_file_name}")
            return ticker_file_path

        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return None

    # --- Private Helpers ---

    @staticmethod
    def _parse_numeric(val: Any) -> float:
        if val is None or pd.isna(val):
            return 0.0
        val_str = str(val).replace("$", "").replace(",", "").replace("%", "").strip()
        try:
            return float(val_str)
        except ValueError:
            return 0.0
    
    @staticmethod
    def _parse_volume(val: Any) -> float:
        """Parses volume integer/string and scales to millions."""
        if val is None or pd.isna(val):
            return 0.0
        val_str = str(val).replace("$", "").replace(",", "").replace("%", "").strip()
        try:
            return float(val_str) / 1_000_000
        except ValueError:
            return 0.0

    @staticmethod
    def _parse_marketcap(val: Any) -> float:
        if val is None or pd.isna(val):
            return 0.0
        val_str = str(val).replace("$", "").replace(",", "").strip()
        try:
            return float(val_str) / 1_000_000
        except ValueError:
            return 0.0

    def _fetch_from_nasdaq(self) -> pd.DataFrame:
        session = requests.Session()
        res = session.get(self._API_URL, headers=self._HEADERS, timeout=15)
        res.raise_for_status()
        payload = res.json()

        data = payload.get("data")
        if not data or not data.get("rows"):
            raise ValueError("NASDAQ API returned empty payload.")

        df = pd.DataFrame(data["rows"])
        df["marketCapNumeric"] = df["marketCap"].apply(self._parse_marketcap)
        df["lastSaleNumeric"] = df["lastsale"].apply(self._parse_numeric)
        df["volumeNumeric"] = df["volume"].apply(self._parse_volume)

        return df

    def _resolve_sector(self, sector_input: str | None) -> str | None:
        if not sector_input or not isinstance(sector_input, str):
            return None
        clean_key = sector_input.strip().upper().replace("_", " ")
        return self.SECTORS.get(clean_key)

    def _clear_ticker_data_folder(self, folder_path: Path) -> None:
        csv_files = list(folder_path.glob("*.csv"))
        if not csv_files:
            return

        print(f"Clearing {len(csv_files)} existing file(s) from {folder_path}...")
        for file in csv_files:
            try:
                file.unlink()
                print(f"  Deleted: {file.name}")
            except Exception as e:
                print(f"  Failed to delete {file.name}: {e}")