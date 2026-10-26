# Finnbot: An AI Financial Advisor for Personalized Portfolio Digests and Alerts

## Preview card

Built an AI financial advisor that screens 7,000 US stocks, reviews live brokerage holdings, and sends personalized HTML email digests and Telegram alerts using Claude, DeepSeek, and Gemini.

## Description

I designed Finnbot as a multi-user investment tool that does the daily research for each person it serves. Twice every trading day it sends each user a personalized HTML email digest: it pulls their live holdings through the SnapTrade brokerage API, has Claude review each position against a rubric worked out in code (trend, RSI, volatility, earnings dates, news), and labels it hold, buy more, profit sell, risky, or urgent sell. Alongside each portfolio, it screens about 7,000 US listings through Yahoo Finance, Finnhub, and Twelve Data down to a few dozen candidates, has an LLM score them, and picks long-term and swing-trade ideas under diversity rules I set, such as different industries and a minimum share of non-tech picks. Between digests, an hourly Telegram watchdog re-checks only the stocks that have moved, and a price tripwire catches sudden swings. New users are onboarded from a sign-up form, and every setting can be tuned per user or from a shared spreadsheet, so the system is built to scale beyond its first users.

Most of the engineering went into making it dependable and cheap enough to run every day. It runs unattended on a Google Cloud VM, with scheduled jobs sharing a lock, healthchecks.io monitoring, a maintenance mode for safe updates, nightly backups, and more than 400 automated tests. Every API call's cost is tracked per user, and prompt caching, model choices made from side-by-side comparisons, and re-evaluating only what changed keep the running cost low. The digest is a hand-built HTML and AMP email, rendered from Jinja templates, designed to read well on a phone in dark mode and to fit under Gmail's 102 KB clipping limit by dropping content in a set priority order. I designed the overall architecture and pipeline in Python, and used Claude Code to implement individual components within that design.
