import os
import pandas as pd
from dotenv import load_dotenv
from snaptrade_client import SnapTrade, SnapTradeAuth


class QuestradePortfolioFetcher:
    def __init__(self, env_path: str = ".env"):
        load_dotenv(dotenv_path=env_path)

        client_id = os.getenv("SNAPTRADE_CLIENT_ID")
        consumer_key = os.getenv("SNAPTRADE_CONSUMER_KEY")

        if not client_id or not consumer_key:
            raise ValueError("Missing SNAPTRADE_CLIENT_ID or SNAPTRADE_CONSUMER_KEY in .env file.")
        print("Attempting to connect to SnapTrade API...")
        self.client = SnapTrade(
            auth=SnapTradeAuth.personal_api_key(
                client_id=client_id,
                consumer_key=consumer_key
            )
        )

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
            accounts = response.body if hasattr(response, "body") and isinstance(response.body, list) else response if isinstance(response, list) else []
            return accounts
        except Exception as e:
            print(f"[ERROR] Failed to fetch accounts: {e}")
            return []

    def fetch_positions_for_account(self, account_id: str) -> list:
        try:
            response = self.client.account_information.get_all_account_positions(
                account_id=account_id
            )
            raw = response.body if hasattr(response, "body") else response
            raw_dict = self._to_dict(raw)
            
            if isinstance(raw_dict, dict) and "results" in raw_dict:
                return raw_dict["results"], raw_dict.get("data_freshness", {})
            elif isinstance(raw, list):
                return raw, {}
            return [], {}
        except Exception as e:
            print(f"[ERROR] Failed fetching positions for account ID {account_id}: {e}")
            return [], {}

    def get_holdings_df(self) -> pd.DataFrame:
        """Retrieves all available raw and computed position metrics as a pandas DataFrame."""
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

                # Core instrument metrics
                instrument = self._to_dict(pos_dict.get("instrument", {}))
                figi = self._to_dict(instrument.get("figi_instrument", {}))

                # Quantitative fields
                units = float(pos_dict.get("units") or 0.0)
                price = float(pos_dict.get("price") or 0.0)
                cost_basis_per_share = float(pos_dict.get("cost_basis") or pos_dict.get("average_purchase_price") or 0.0)

                # Derived position values
                market_value = units * price
                total_cost_basis = units * cost_basis_per_share
                unrealized_pnl = market_value - total_cost_basis
                unrealized_pnl_pct = ((unrealized_pnl / total_cost_basis) * 100) if total_cost_basis > 0 else 0.0

                record = {
                    # Account Metadata
                    "account_name": account_label,
                    "account_id": account_id,
                    
                    # Position Instrument Metadata
                    "symbol": instrument.get("symbol") or instrument.get("raw_symbol"),
                    "description": instrument.get("description"),
                    "asset_kind": instrument.get("kind"),
                    "exchange": instrument.get("exchange"),
                    "currency": pos_dict.get("currency") or instrument.get("currency"),
                    "instrument_id": instrument.get("id"),
                    "figi_code": figi.get("figi_code"),
                    "figi_share_class": figi.get("figi_share_class"),
                    
                    # Quantitative Data
                    "shares": units,
                    "current_price": price,
                    "avg_cost_basis": cost_basis_per_share,
                    
                    # Computed Metrics
                    "market_value": market_value,
                    "total_cost_basis": total_cost_basis,
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                    
                    # Metadata
                    "data_as_of": freshness.get("as_of")
                }
                records.append(record)

        df = pd.DataFrame(records)
        return df

    def simplify_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filters the holdings DataFrame down to core P&L columns."""
        target_cols = ["symbol", "unrealized_pnl", "unrealized_pnl_pct"]
        
        if df.empty:
            return pd.DataFrame(columns=target_cols)
            
        return df[target_cols].copy()


if __name__ == "__main__":
    fetcher = QuestradePortfolioFetcher()
    
    # 1. Fetch full DataFrame
    df_full = fetcher.get_holdings_df()
    
    # 2. Extract simplified DataFrame
    df_simple = fetcher.simplify_data(df_full)

    # 3. Export to CSVs
    df_full.to_csv("questrade_positions_full.csv", index=False)
    df_simple.to_csv("questrade_positions_simple.csv", index=False)

    print("\n================ SIMPLIFIED DATAFRAME ================")
    print(df_simple)
    print("======================================================")