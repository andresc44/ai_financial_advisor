# Claude-authored (2026-10-22): importing sign-up form answers into .env and ACTIVE_USERS (src/users/import_signup.py)
import ast

import pytest
from dotenv import dotenv_values

import src.users.import_signup as signup
from src.config.parameters import params_dict

# The answers sheet's header, as the first form (docs/finnbot_signup_form.gs) created it: titles repeat. The model
# questions were dropped on 2026-10-22 (NEW_HEADER); an answer from either form clears per-user model choices
HEADER = ["Timestamp", "Your first name", "Email address for the digest", "Which digests do you want?",
          "How should Finnbot see your holdings?", "Please confirm", "Please confirm your sheet's layout",
          "How did you share the sheet?", "Sheet ID", "Your timezone (optional)",
          "Brokerage name to show in your digest", "Your timezone", "Home currency", "News for each holding",
          "Which AI model should review your holdings?", "Claude model", "Effort (how long it thinks)",
          "DeepSeek model", "Effort (how long it thinks)", "Please confirm", "Do you want Telegram messages?",
          "Hours between checks", "When should it run?", "Tripwire", "Please confirm",
          "Anything else Andres should know? (optional)", "Email address"]
MODEL_QUESTIONS = {"Which AI model should review your holdings?", "Claude model", "Effort (how long it thinks)",
                   "DeepSeek model"}
NEW_HEADER = [title for title in HEADER if title not in MODEL_QUESTIONS]
CLEARED = dict.fromkeys(["holdings_llm_model", "holdings_llm_effort", "holdings_claude_model", "holdings_claude_effort"])
BOTH = "Both: Pre-Market (starts 7:00 AM ET) and Post-Market (starts 4:10 PM ET)"
SAM = {
    "Timestamp": "10/22/2026 10:00:00", "Your first name": "sam", "Email address for the digest": "sam@example.com",
    "Which digests do you want?": BOTH,
    "How should Finnbot see your holdings?": "Google Sheets: I'll keep my holdings in a spreadsheet",
    "How did you share the sheet?": "Privately, with Finnbot's service-account email (recommended)",
    "Sheet ID": "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "Brokerage name to show in your digest": "My portfolio",
    "Your timezone": "Europe/London", "Home currency": "GBP",
    "News for each holding": "On (default): about $0.012 per stock per digest",
    "Which AI model should review your holdings?": "Choose a Claude model (needs an Anthropic key)",
    "Claude model": "Claude Opus 5.5: about $0.054, stronger reasoning", ("Effort (how long it thinks)", 0): "High",
    "Do you want Telegram messages?": "Yes, with the watchdog: urgent sell alerts, earnings reminders, and notices",
    "Hours between checks": "2",
    "When should it run?": "Extended: 4:00 AM-8:00 PM ET on trading days (pre-market and after-hours too)",
    "Tripwire": "Off", "Anything else Andres should know? (optional)": "I hold mostly ETFs.",
}
KIM = {  # Ideas only
    "Your first name": "Kim", "Email address for the digest": "kim@example.com",
    "Which digests do you want?": "Pre-Market only (starts 7:00 AM ET, before the US market opens)",
    "How should Finnbot see your holdings?": "Ideas only: no holdings, free", "Your timezone (optional)": "America/Toronto",
}
LEE = {  # A sheet shared by link, a DeepSeek model, no Telegram
    "Your first name": "Lee", "Email address for the digest": "lee@example.com", "Which digests do you want?": BOTH,
    "How should Finnbot see your holdings?": "Google Sheets: I'll keep my holdings in a spreadsheet",
    "How did you share the sheet?": "By link (\"Anyone with the link can view\"): I'll send the sheet's ID to Andres "
                                    "privately, since anyone with it can see my holdings",
    "Brokerage name to show in your digest": "Wealthsimple", "Your timezone": "America/Bogota", "Home currency": "COP",
    "News for each holding": "Off: saves that cost",
    "Which AI model should review your holdings?": "Choose a DeepSeek model (needs a DeepSeek key; cheapest, data goes to DeepSeek)",
    "DeepSeek model": "DeepSeek V4 Pro: about $0.007, stronger", ("Effort (how long it thinks)", 1): "None (no thinking)",
    "Do you want Telegram messages?": "No Telegram",
}
ENV = ("# Shared\r\nANDRES_EMAIL=andres@example.com\r\nANDRES_GEMINI_API_KEY=secret-gemini-value\r\n\r\n"
       "# --- Steven ---\r\nSTEVEN_HOLDINGS_SOURCE=SHEETS\r\nSTEVEN_EMAIL=${ANDRES_EMAIL}  # TEMP: Andres's\r\n"
       "STEVEN_HOLDINGS_LLM_MODEL=deepseek-flash\r\n")
PARAMS = ('params_dict = {\r\n    "ACTIVE_USERS": [\r\n                    # "Andres",\r\n                    "Steven",\r\n'
          '                    # "Kim",  # Paused\r\n                    ],\r\n    "OTHER": 1,\r\n}\r\n')


def answer(values, row_number=2, header=HEADER):
    """A response row in the sheet's column order; ("title", n) sets the nth of a repeated title."""
    seen, row = {}, []
    for title in header:
        n = seen[title] = seen.get(title, -1) + 1
        row.append(values.get((title, n), values.get(title, "") if n == 0 else ""))
    return signup.Answer.from_row(header, row, row_number)


@pytest.fixture
def files(tmp_path):
    env, params = tmp_path / ".env", tmp_path / "parameters.py"
    env.write_bytes(ENV.encode())
    params.write_bytes(PARAMS.encode())
    return env, params


def active_users(params):
    tree = ast.parse(params.read_text(encoding="utf-8"))
    return next(ast.literal_eval(node.values[i]) for node in ast.walk(tree) if isinstance(node, ast.Dict)
                for i, key in enumerate(node.keys) if getattr(key, "value", None) == "ACTIVE_USERS")


def test_each_answer_becomes_the_matching_settings():
    sam = signup.plan_from_answer(answer(SAM))
    assert sam.name == "Sam" and sam.wants_telegram and not sam.sheet_by_link
    assert sam.settings == {
        "destination_email": "sam@example.com", "Receive_Premarket_Digest": "True", "Receive_Postmarket_Digest": "True",
        "holdings_source": "SHEETS", "home_timezone": "Europe/London",
        "google_sheets_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "account_company": "My portfolio",
        "home_currency": "GBP", "receive_holding_news": "True", **CLEARED,  # A model answered on the old form: ignored
        "watchdog_interval_hours": "2", "watchdog_session": "extended", "watchdog_tripwire": "false"}
    assert sam.notes == ["Their note: I hold mostly ETFs."]
    # The new form has no model questions: the same settings
    new_form = {title: value for title, value in SAM.items() if (title[0] if isinstance(title, tuple) else title) not in MODEL_QUESTIONS}
    assert signup.plan_from_answer(answer(new_form, header=NEW_HEADER)).settings == sam.settings
    lee = signup.plan_from_answer(answer(LEE))
    # No Telegram turns the watchdog off
    assert all(lee.settings[key] is None for key in CLEARED)
    assert lee.sheet_by_link and "google_sheets_id" not in lee.settings and not lee.wants_telegram
    assert lee.settings["watchdog_interval_hours"] == "0" and lee.settings["receive_holding_news"] == "False"
    kim = signup.plan_from_answer(answer(KIM))
    assert kim.settings == {"destination_email": "kim@example.com", "Receive_Premarket_Digest": "True",
                            "Receive_Postmarket_Digest": "False", "holdings_source": "NO_HOLDINGS",
                            "home_timezone": "America/Toronto"}  # Ideas only: no holdings settings at all


def test_a_holdings_user_waits_for_their_keys_then_is_activated(files, capsys):
    env, params = files
    result = signup.apply_answer(answer(SAM), env, params)
    values = dotenv_values(env)
    assert values["SAM_INSTITUTION"] == "My portfolio" and "SAM_HOLDINGS_LLM_MODEL" not in values  # Never written
    raw = env.read_bytes().decode()
    assert "SAM_INSTITUTION='My portfolio'" in raw and "\r\n# --- Sam (from the sign-up form" in raw
    assert raw.count("\n") == raw.count("\r\n")  # Line endings kept
    missing = {key for status, key, _ in result.rows if status == "MISSING"}
    assert missing == {"SAM_ANTHROPIC_API_KEY", "SAM_GEMINI_API_KEY", "SAM_TELEGRAM_CHAT_ID"}
    # 2026-10-22: each missing API key their choices need is written as None, so the gap shows in .env (LLM["HOLDINGS"]
    # is a Claude model, so no DeepSeek key; the chat ID isn't an API key)
    values = dotenv_values(env)
    assert values["SAM_ANTHROPIC_API_KEY"] == values["SAM_GEMINI_API_KEY"] == "None"
    # 2026-10-24: the chat ID gets an empty line instead, a spot to fill in
    assert "SAM_DEEPSEEK_API_KEY" not in values and values["SAM_TELEGRAM_CHAT_ID"] == ""
    assert not result.activated and "not activated yet" in result.activation and "Sam" not in active_users(params)
    signup._print_result(result, answer(SAM), dry_run=False)
    printed = capsys.readouterr().out
    assert "MISSING  SAM_GEMINI_API_KEY" in printed and "secret-gemini-value" not in printed

    # Keys arrive: Andres replaces the placeholders, and the next check finds nothing missing
    env.write_bytes(env.read_bytes().replace(b"SAM_ANTHROPIC_API_KEY=None", b"SAM_ANTHROPIC_API_KEY=sk-ant-x")
                    .replace(b"SAM_GEMINI_API_KEY=None", b"SAM_GEMINI_API_KEY=AIza-x")
                    .replace(b"SAM_TELEGRAM_CHAT_ID=\r\n", b"SAM_TELEGRAM_CHAT_ID=123\r\n"))
    again = signup.apply_answer(answer(SAM), env, params, write=False)
    assert again.activated and active_users(params) == ["Steven", "Sam"]


def test_an_ideas_only_user_is_activated_at_once_even_when_commented_out(files):
    env, params = files
    result = signup.apply_answer(answer(KIM), env, params)
    assert result.activated and "uncommented" in result.activation
    assert active_users(params) == ["Steven", "Kim"]
    assert dotenv_values(env)["KIM_HOLDINGS_SOURCE"] == "NO_HOLDINGS"


def test_the_form_replaces_a_stand_in_when_the_name_and_email_match(files):
    env, params = files
    steven = {**SAM, "Your first name": "Steven", "Email address for the digest": "andres@example.com",
              "Which AI model should review your holdings?": "The default: Claude Haiku 5.5 ..."}
    result = signup.apply_answer(answer(steven), env, params)
    assert result.status == "applied" and result.existing
    raw = env.read_bytes().decode()
    assert "STEVEN_EMAIL=andres@example.com\r\n" in raw and "${ANDRES_EMAIL}" not in raw
    assert "STEVEN_HOLDINGS_LLM_MODEL=\r\n" in raw  # Per-user models are cleared: LLM["HOLDINGS"] applies
    change = next(c for c in result.changes if c.key == "STEVEN_EMAIL")
    assert change.old == "${ANDRES_EMAIL}"


def test_a_name_taken_by_another_email_isnt_imported_without_overwrite(files):
    env, params = files
    before = env.read_bytes()
    stranger = {**KIM, "Your first name": "Steven", "Email address for the digest": "someone@example.com"}
    result = signup.apply_answer(answer(stranger), env, params)
    assert result.status == "conflict" and result.conflict_email == "andres@example.com"
    assert env.read_bytes() == before
    assert signup.apply_answer(answer(stranger), env, params, overwrite=True).status == "applied"


def test_activate_user_handles_each_layout(tmp_path):
    path = tmp_path / "p.py"
    path.write_text('params_dict = {"ACTIVE_USERS": ["Andres"], "X": 1}\n')
    assert signup.activate_user(path, "Kim") == (True, "Kim added to ACTIVE_USERS.")
    assert active_users(path) == ["Andres", "Kim"]
    assert signup.activate_user(path, "kim")[0] is False  # Already there (any case)
    path.write_text('params_dict = {\n    "ACTIVE_USERS": ["Andres",\n                    "Koby"  # Ideas only\n'
                    '                    ],\n}\n')
    assert signup.activate_user(path, "Lee")[0] and active_users(path) == ["Andres", "Koby", "Lee"]
    assert '"Koby",  # Ideas only' in path.read_text()  # A comma added before the comment
    assert signup.activate_user(path, "Zed", dry_run=True)[0] and "Zed" not in active_users(path)


def test_set_active_users_rewrites_the_whole_list(files):
    _, params = files
    assert signup.set_active_users(params, ["Sam", "Kim"])
    text = params.read_bytes().decode()
    assert active_users(params) == ["Sam", "Kim"] and "Andres" not in text and '"OTHER": 1' in text
    assert "Synced from the sign-up sheet" in text and text.count("\n") == text.count("\r\n")
    assert not signup.set_active_users(params, ["Sam", "Kim"])  # Already so: not rewritten
    assert signup.set_active_users(params, []) and active_users(params) == []


def test_the_digests_sync_keeps_everything_matching_the_sheet(files, monkeypatch, telegram):
    # Andres, 2026-10-22: the sheet is the reference. ACTIVE_USERS is its users with nothing required missing.
    env, params = files
    answers = [answer(SAM, 2), answer(KIM, 3)]
    monkeypatch.setattr(signup, "read_answers", lambda sheet_id: answers)
    monkeypatch.setitem(params_dict, "ACTIVE_USERS", ["Steven"])
    monkeypatch.setattr(signup, "USER_PROFILES", {"Steven": {}})
    sync = lambda: signup.sync_signups(env, params, sheet_id="sheet")

    assert sync() == ["Kim"]  # Kim (ideas only) is complete; Sam waits for keys; Steven isn't in the sheet
    assert active_users(params) == ["Kim"] and params_dict["ACTIVE_USERS"] == ["Kim"]
    # Profiles: Kim's, and the admin's, so the notices reach Andres even while he isn't active (the Telegram sender
    # looks chat IDs up there); not Steven's any more
    assert set(signup.USER_PROFILES) == {"Kim", "Andres"} and "Andres" not in params_dict["ACTIVE_USERS"]
    assert dotenv_values(env)["STEVEN_HOLDINGS_SOURCE"] == "SHEETS"  # Inactive, but his settings stay
    sent = [body for _, body in telegram.messages]
    assert any("New Finnbot sign-up: Sam" in body and "SAM_GEMINI_API_KEY" in body for body in sent)
    # 2026-10-22: a user added says so, with what they signed up for
    kim = next(body for body in sent if "New Finnbot user added: Kim" in body)
    assert "- Holdings: ideas only" in kim and "- Digests: Pre-Market" in kim and "Telegram" not in kim
    assert any("Steven is no longer active" in body and "aren't in the sign-up sheet" in body for body in sent)

    # Nothing changed: nothing is written or sent again
    written, count = env.read_bytes(), len(telegram.messages)
    assert sync() == [] and env.read_bytes() == written and len(telegram.messages) == count

    # A hand edit to a setting the form covers goes back to the sheet's value; once Sam's keys are in, Sam is active
    raw = (env.read_bytes().decode().replace("SAM_HOME_CURRENCY=GBP", "SAM_HOME_CURRENCY=USD")
           .replace("SAM_ANTHROPIC_API_KEY=None", "SAM_ANTHROPIC_API_KEY=sk-ant-x")
           .replace("SAM_GEMINI_API_KEY=None", "SAM_GEMINI_API_KEY=AIza-x"))
    env.write_bytes((raw + "SAM_TELEGRAM_CHAT_ID=1\r\n").encode())
    assert sync() == ["Sam"]
    assert dotenv_values(env)["SAM_HOME_CURRENCY"] == "GBP" and active_users(params) == ["Sam", "Kim"]
    sam = next(body for _, body in telegram.messages if "New Finnbot user added: Sam" in body)
    assert "- Holdings: Google Sheets" in sam and "- Digests: Pre-Market and Post-Market" in sam
    assert "- Home currency: GBP" in sam and "- Telegram: yes, watchdog every 2 h (extended)" in sam

    # Sam edits the answer: applied, and announced once more
    answers[0] = answer({**SAM, "Home currency": "EUR"}, 2)
    sync()
    assert dotenv_values(env)["SAM_HOME_CURRENCY"] == "EUR"
    assert sum("Updated Finnbot sign-up: Sam" in body for _, body in telegram.messages) == 1

    # Kim leaves the sheet: no longer active
    answers.pop()
    sync()
    assert active_users(params) == ["Sam"]


def test_the_sync_never_stops_the_digest_or_empties_the_list(files, monkeypatch, capsys):
    env, params = files
    before = params.read_bytes()
    monkeypatch.setattr(signup, "read_answers", lambda sheet_id: (_ for _ in ()).throw(RuntimeError("sheet gone")))
    assert signup.sync_signups(env, params, sheet_id="sheet") == []
    assert "Sign-up sync failed" in capsys.readouterr().out and params.read_bytes() == before
    monkeypatch.setattr(signup, "read_answers", lambda sheet_id: [])  # No answers at all: suspicious, so no change
    assert signup.sync_signups(env, params, sheet_id="sheet") == [] and params.read_bytes() == before
    monkeypatch.delenv(signup.SHEET_ID_KEY, raising=False)
    assert signup.sync_signups(env, params) == []  # Not set up: nothing happens


def test_a_none_placeholder_counts_as_missing_everywhere(monkeypatch):
    # Claude-authored (2026-10-22): KEY=None marks a key the sync found missing; nothing may use it as a real key
    from src.config.check_config import _env
    from src.personalized.user_profiles import env_setting
    for value, expected in (("None", None), ("  ", None), ("sk-real", "sk-real"), (" AIza-x ", "AIza-x")):
        monkeypatch.setenv("X_GEMINI_API_KEY", value)
        assert env_setting("X_GEMINI_API_KEY") == expected and _env("X_GEMINI_API_KEY") == expected
    lee = signup.plan_from_answer(answer(LEE))
    # 2026-10-22: the key LLM["HOLDINGS"] needs (Anthropic), whatever model was set per user before
    rows = signup.missing_report("Lee", {"LEE_HOLDINGS_SOURCE": "SHEETS", "LEE_HOLDINGS_LLM_MODEL": "deepseek-v4-pro",
                                         "LEE_ANTHROPIC_API_KEY": "None"}, lee)
    assert ("MISSING", "LEE_ANTHROPIC_API_KEY") in [(status, key) for status, key, _ in rows]
    assert ("MISSING", "LEE_DEEPSEEK_API_KEY") not in [(status, key) for status, key, _ in rows]


def test_a_holdings_user_gets_placeholders_for_the_keys_the_holdings_model_needs(files):
    # 2026-10-22: Lee answered the old form's DeepSeek question, but LLM["HOLDINGS"] (a Claude model) applies to
    # everyone, so the Anthropic key gets a placeholder, and no DeepSeek key. 2026-10-23: Lee turned the news off, and
    # the portfolio review no longer uses Gemini, so no Gemini key is needed either
    env, params = files
    signup.apply_answer(answer(LEE), env, params)
    values = dotenv_values(env)
    assert values["LEE_ANTHROPIC_API_KEY"] == "None"
    assert "LEE_DEEPSEEK_API_KEY" not in values and "LEE_GEMINI_API_KEY" not in values
    before = env.read_bytes()
    signup.apply_answer(answer(LEE), env, params)  # Already there: not written again
    assert env.read_bytes() == before


def test_a_telegram_user_gets_a_chat_id_line_and_others_dont(files):
    # Claude-authored (2026-10-24, Andres): an empty spot when .env has none. 2026-10-25: only for a user who chose
    # Telegram on the form; a set ID stays
    env, params = files
    signup.apply_answer(answer(LEE), env, params)  # No Telegram
    assert "LEE_TELEGRAM_CHAT_ID" not in dotenv_values(env)
    signup.apply_answer(answer(SAM), env, params)  # Telegram
    assert dotenv_values(env)["SAM_TELEGRAM_CHAT_ID"] == ""
    signup.apply_answer(answer(SAM), env, params)  # Applied again: still one line
    assert env.read_text().count("SAM_TELEGRAM_CHAT_ID=") == 1

    env.write_text(env.read_text().replace("SAM_TELEGRAM_CHAT_ID=", "SAM_TELEGRAM_CHAT_ID=12345"))
    signup.apply_answer(answer(SAM), env, params)
    assert dotenv_values(env)["SAM_TELEGRAM_CHAT_ID"] == "12345"


def test_cron_runs_sync_settings_then_signups_and_show_a_new_active_user_on_the_sheet(monkeypatch):
    # Claude-authored (2026-10-25, Andres): both cron jobs start with this; when a user becomes active (their keys were
    # filled in), the Parameters tab is written again so its ACTIVE_USERS row shows them in the same run
    import src.ops.cron_start as cron_start

    calls = []
    monkeypatch.setitem(cron_start.params_dict, "ACTIVE_USERS", ["Andres"])
    monkeypatch.setattr(cron_start, "sync_settings", lambda: calls.append("settings"))

    def activate():
        calls.append("signups")
        cron_start.params_dict["ACTIVE_USERS"].append("Sam")

    monkeypatch.setattr(cron_start, "sync_signups", activate)
    cron_start.sync_at_start()
    assert calls == ["settings", "signups", "settings"]

    calls.clear()
    monkeypatch.setattr(cron_start, "sync_signups", lambda: calls.append("signups"))  # Nobody changes
    cron_start.sync_at_start()
    assert calls == ["settings", "signups"]
    calls.clear()
    cron_start.sync_at_start(signups=False)  # A manual digest run: the settings only
    assert calls == ["settings"]
