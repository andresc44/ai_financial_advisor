# File to hold the locks shared by code that runs several users at once (the digest and the watchdog use one thread per user).
"""
Authorship: Written by Claude (Claude Opus 5.5 via Claude Code) on 2026-10-15, at Andres's request, when the digest
and the watchdog started processing users in parallel threads.

Why a lock for market data: Finnhub and Twelve Data each use one shared key, and their fetchers call them back to
back with only a short sleep (FINNHUB_RATE_LIMIT_SLEEP). Running several users' fetches at once would multiply the
request rate on those keys. Holding this lock while fetching keeps the rate the same as a single-user run, makes the
market-context cache's read-modify-write safe, and lets a user who waited find the symbols another user just fetched
in that cache instead of fetching them again. Everything else (holdings, Claude, Gemini, Telegram) uses per-user
keys or no keys and runs in parallel.
"""
import threading

MARKET_DATA_LOCK = threading.Lock()
