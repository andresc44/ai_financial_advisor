import os
from dotenv import load_dotenv
from twelvedata import TDClient
# https://github.com/twelvedata/twelvedata-python
# https://twelvedata.com/docs/introduction/libraries
# Load API key from .env file
load_dotenv()

api_key = os.getenv("TWELVEDATA_API_KEY")
if not api_key:
    raise ValueError("TWELVEDATA_API_KEY not found in environment variables.")

# Initialize Twelve Data client
td = TDClient(apikey=api_key)

# Chain indicators onto time series
ts = (
    td.time_series(symbol="ENB", interval="1day", outputsize=100)
    .with_ema(time_period=20)
    .with_rsi(time_period=14)
    .with_mfi(time_period=14)
    .with_stochrsi(time_period=14)
)

# Render interactive Plotly figure
fig = ts.as_plotly_figure()

# Display interactive chart in browser or Jupyter Notebook
fig.show()

# Save interactive chart as HTML
fig.write_html("CHRW_interactive_chart.html")