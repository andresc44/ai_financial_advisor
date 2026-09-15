# ticker_client = Tickers()

# # --- Step 1: Query available parameters ---
# # sectors = ticker_client.get_available_sectors()
# # regions_with_countries = ticker_client.get_available_regions(include_countries=True)
# # industries = ticker_client.get_available_industries()
# # print("Available Sectors:", sectors)
# # print("Available Regions:", list(regions_with_countries.keys()))
# # print("Available industries:", industries)

# # --- Step 2: Pass all filter arguments explicitly ---

# file_path = ticker_client.fetch_ticker_data(
#     mktcap_min=params_dict["MKTCAP_MIN"],        # Minimum market cap in Millions USD
#     mktcap_max=params_dict["MKTCAP_MAX"],        # Maximum market cap in Millions USD
#     volume_min=params_dict["VOLUME_MIN"],        # Minimum trading volume in Millions
#     volume_max=params_dict["VOLUME_MAX"],        # Maximum trading volume in Millions
#     lastsale_min=params_dict["LASTSALE_MIN"],    # Minimum share price USD
#     lastsale_max=params_dict["LASTSALE_MAX"],    # Maximum share price USD
#     region=params_dict["REGION"],                # Takes priority over 'country'
#     country=params_dict["COUNTRY"],              # Ignored when region is defined
#     sector=params_dict["SECTOR"],                # Takes priority over 'industry'
#     industry=params_dict["INDUSTRY"],            # Ignored when sector is defined
#     clear_existing_data=params_dict["CLEAR_EXISTING_DATA"],  # Deletes previous CSV exports in output dir
# )

# print(f"Filtered file generated: {file_path}")

#https://github.com/twelvedata/twelvedata-python
def fetch_advanced_momentum(
    symbol: str = "HPE", interval: str = "1day"
) -> pd.DataFrame:
    api_key = os.getenv("TWELVEDATA_API_KEY")
    if not api_key:
        raise ValueError("TWELVEDATA_API_KEY not found in .env file.")

    # Endpoint names corrected according to Twelve Data API docs
    indicators = {
        "CMO": ("cmo", {"time_period": 14}),
        "Ultimate Oscillator": (
            "ultosc",
            {"time_period1": 7, "time_period2": 14, "time_period3": 28},
        ),
        "MFI": ("mfi", {"time_period": 14}),
        "PPO": ("ppo", {"fast_period": 12, "slow_period": 26}),
        "StochRSI": ("stochrsi", {"time_period": 14}),
        "KST": ("kst", {}),
    }

    parsed_rows = []

    for label, (endpoint, params) in indicators.items():
        url = f"https://api.twelvedata.com/{endpoint}"
        query_params = {
            "symbol": symbol,
            "interval": interval,
            "apikey": api_key,
            "outputsize": 1,
            **params,
        }

        try:
            response = requests.get(url, params=query_params, timeout=10)

            # Check if response is valid JSON
            if response.status_code == 200:
                res = response.json()
                if "values" in res and len(res["values"]) > 0:
                    latest = res["values"][0]
                    latest.pop("datetime", None)

                    # Unpack multiple returned metrics (e.g., StochRSI -> %K, %D)
                    for metric_name, val in latest.items():
                        parsed_rows.append({
                            "Indicator": label,
                            "Metric": metric_name,
                            "Value": (
                                round(float(val), 4)
                                if val is not None
                                else "N/A"
                            ),
                        })
                else:
                    err_msg = res.get("message", "No data returned")
                    parsed_rows.append({
                        "Indicator": label,
                        "Metric": "Error",
                        "Value": err_msg,
                    })
            else:
                parsed_rows.append({
                    "Indicator": label,
                    "Metric": "Error",
                    "Value": f"HTTP {response.status_code}",
                })

        except Exception as e:
            parsed_rows.append(
                {"Indicator": label, "Metric": "Error", "Value": str(e)}
            )

        # Pause 1 sec between calls to respect 8 reqs/min free rate limit
        time.sleep(1)

    return pd.DataFrame(parsed_rows)

    ticker = "HPE"
    print(f"Fetching advanced momentum indicators for {ticker}...\n")
    df_momentum = fetch_advanced_momentum(ticker)
    print(df_momentum.to_string(index=False))