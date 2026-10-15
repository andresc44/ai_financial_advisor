USER_PROFILES = {
    "Andres": {
        "account_company": "ANDRES_INSTITUTION",
        "destination_email": "ANDRES_EMAIL",
        "home_timezone": "ANDRES_TIMEZONE",
        "telegram_chat_id": "ANDRES_TELEGRAM_CHAT_ID",
        "holdings_source": "ANDRES_HOLDINGS_SOURCE", #options for holdings_source: "SNAPTRADE", "SHEETS", "NO_HOLDINGS"
        "snaptrade_client_id": "ANDRES_SNAPTRADE_CLIENT_ID",
        "snaptrade_consumer_key": "ANDRES_SNAPTRADE_CONSUMER_KEY",
        "google_sheets_id": "ANDRES_SHEETS_ID",
        "Receive_Premarket_Digest": "ANDRES_RECEIVE_PREMARKET_DIGEST",
        "Receive_Postmarket_Digest": "ANDRES_RECEIVE_POSTMARKET_DIGEST",
        # Claude-authored (2026-10-13): per-user watchdog + model settings (resolved by user_settings.py;
        # unset or invalid values fall back to params_dict["WATCHDOG"] / ["CLAUDE_CONFIG"]). See docs/watchdog.md.
        "watchdog_interval_hours": "ANDRES_WATCHDOG_INTERVAL_HOURS",  # int hours between checks; 0 = off
        "watchdog_session": "ANDRES_WATCHDOG_SESSION",                # "regular" | "extended" | "24_7"
        "watchdog_tripwire": "ANDRES_WATCHDOG_TRIPWIRE",              # true/false: hourly price check between checks
        "holdings_claude_model": "ANDRES_HOLDINGS_CLAUDE_MODEL",      # must be in params_dict["ALLOWED_CLAUDE_MODELS"]
        "holdings_claude_effort": "ANDRES_HOLDINGS_CLAUDE_EFFORT",    # low | medium | high | xhigh | max
        # Claude-authored (2026-10-14): this user's own Anthropic key (holdings evaluation) and Gemini key
        # (digest holding news). Neither falls back to the shared keys.
        "anthropic_api_key": "ANDRES_ANTHROPIC_API_KEY",
        "gemini_api_key": "ANDRES_GEMINI_API_KEY",
    },
    "Mauricio": {
        "account_company": "MAURICIO_INSTITUTION",
        "destination_email": "MAURICIO_EMAIL",
        "home_timezone": "MAURICIO_TIMEZONE",
        "telegram_chat_id": "MAURICIO_TELEGRAM_CHAT_ID",
        "holdings_source": "MAURICIO_HOLDINGS_SOURCE",
        "snaptrade_client_id": "MAURICIO_SNAPTRADE_CLIENT_ID",
        "snaptrade_consumer_key": "MAURICIO_SNAPTRADE_CONSUMER_KEY",
        "google_sheets_id": "MAURICIO_SHEETS_ID",
        "Receive_Premarket_Digest": "MAURICIO_RECEIVE_PREMARKET_DIGEST",
        "Receive_Postmarket_Digest": "MAURICIO_RECEIVE_POSTMARKET_DIGEST",
        # Claude-authored (2026-10-13): see the note on Andres's profile.
        "watchdog_interval_hours": "MAURICIO_WATCHDOG_INTERVAL_HOURS",
        "watchdog_session": "MAURICIO_WATCHDOG_SESSION",
        "watchdog_tripwire": "MAURICIO_WATCHDOG_TRIPWIRE",
        "holdings_claude_model": "MAURICIO_HOLDINGS_CLAUDE_MODEL",
        "holdings_claude_effort": "MAURICIO_HOLDINGS_CLAUDE_EFFORT",
        "anthropic_api_key": "MAURICIO_ANTHROPIC_API_KEY",
        "gemini_api_key": "MAURICIO_GEMINI_API_KEY",
    },
    "Steven": {
        "account_company": "STEVEN_INSTITUTION",
        "destination_email": "STEVEN_EMAIL",
        "home_timezone": "STEVEN_TIMEZONE",
        "telegram_chat_id": "STEVEN_TELEGRAM_CHAT_ID",
        "holdings_source": "STEVEN_HOLDINGS_SOURCE",
        "snaptrade_client_id": "STEVEN_SNAPTRADE_CLIENT_ID",
        "snaptrade_consumer_key": "STEVEN_SNAPTRADE_CONSUMER_KEY",
        "google_sheets_id": "STEVEN_SHEETS_ID",
        "Receive_Premarket_Digest": "STEVEN_RECEIVE_PREMARKET_DIGEST",
        "Receive_Postmarket_Digest": "STEVEN_RECEIVE_POSTMARKET_DIGEST",
        # Claude-authored (2026-10-13): see the note on Andres's profile.
        "watchdog_interval_hours": "STEVEN_WATCHDOG_INTERVAL_HOURS",
        "watchdog_session": "STEVEN_WATCHDOG_SESSION",
        "watchdog_tripwire": "STEVEN_WATCHDOG_TRIPWIRE",
        "holdings_claude_model": "STEVEN_HOLDINGS_CLAUDE_MODEL",
        "holdings_claude_effort": "STEVEN_HOLDINGS_CLAUDE_EFFORT",
        "anthropic_api_key": "STEVEN_ANTHROPIC_API_KEY",
        "gemini_api_key": "STEVEN_GEMINI_API_KEY",
    },
}
