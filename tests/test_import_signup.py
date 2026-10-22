# Claude-authored (2026-10-22): importing sign-up form answers into .env and ACTIVE_USERS (src/import_signup.py)
import ast

import pytest
from dotenv import dotenv_values

import src.import_signup as signup
from src.parameters import params_dict

# The answers sheet's header, as the form (docs/finnbot_signup_form.gs) created it: titles repeat
HEADER = ["Timestamp", "Your first name", "Email address for the digest", "Which digests do you want?",
          "How should Finnbot see your holdings?", "Please confirm", "Please confirm your sheet's layout",
          "How did you share the sheet?", "Sheet ID", "Your timezone (optional)",
          "Brokerage name to show in your digest", "Your timezone", "Home currency", "News for each holding",
          "Which AI model should review your holdings?", "Claude model", "Effort (how long it thinks)",
          "DeepSeek model", "Effort (how long it thinks)", "Please confirm", "Do you want Telegram messages?",
          "Hours between checks", "When should it run?", "Tripwire", "Please confirm",
          "Anything else Andres should know? (optional)", "Email address"]
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


def answer(values, row_number=2):
    """A response row in the sheet's column order; ("title", n) sets the nth of a repeated title."""
    seen, row = {}, []
    for title in HEADER:
        n = seen[title] = seen.get(title, -1) + 1
        row.append(values.get((title, n), values.get(title, "") if n == 0 else ""))
    return signup.Answer.from_row(HEADER, row, row_number)


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
        "home_currency": "GBP", "receive_holding_news": "True", "holdings_llm_model": "claude-opus-5-5",
        "holdings_llm_effort": "high", "watchdog_interval_hours": "2", "watchdog_session": "extended",
        "watchdog_tripwire": "false"}
    assert sam.notes == ["Their note: I hold mostly ETFs."]
    lee = signup.plan_from_answer(answer(LEE))
    # The DeepSeek section's effort is the second "Effort" column; no Telegram turns the watchdog off
    assert (lee.settings["holdings_llm_model"], lee.settings["holdings_llm_effort"]) == ("deepseek-v4-pro", "none")
    assert lee.sheet_by_link and "google_sheets_id" not in lee.settings and not lee.wants_telegram
    assert lee.settings["watchdog_interval_hours"] == "0" and lee.settings["receive_holding_news"] == "False"
    kim = signup.plan_from_answer(answer(KIM))
    assert kim.settings == {"destination_email": "kim@example.com", "Receive_Premarket_Digest": "True",
                            "Receive_Postmarket_Digest": "False", "holdings_source": "NO_HOLDINGS",
                            "home_timezone": "America/Toronto"}
    default = signup.plan_from_answer(answer({**SAM, "Which AI model should review your holdings?": "The default: ..."}))
    assert default.settings["holdings_llm_model"] is None  # Clears an earlier choice


def test_a_holdings_user_waits_for_their_keys_then_is_activated(files, capsys):
    env, params = files
    result = signup.apply_answer(answer(SAM), env, params)
    values = dotenv_values(env)
    assert values["SAM_INSTITUTION"] == "My portfolio" and values["SAM_HOLDINGS_LLM_MODEL"] == "claude-opus-5-5"
    raw = env.read_bytes().decode()
    assert "SAM_INSTITUTION='My portfolio'" in raw and "\r\n# --- Sam (from the sign-up form" in raw
    assert raw.count("\n") == raw.count("\r\n")  # Line endings kept
    missing = {key for status, key, _ in result.rows if status == "MISSING"}
    assert missing == {"SAM_ANTHROPIC_API_KEY", "SAM_GEMINI_API_KEY", "SAM_TELEGRAM_CHAT_ID"}
    # 2026-10-22: each missing API key their choices need is written as None, so the gap shows in .env (a Claude
    # model, so no DeepSeek key; the chat ID isn't an API key)
    values = dotenv_values(env)
    assert values["SAM_ANTHROPIC_API_KEY"] == values["SAM_GEMINI_API_KEY"] == "None"
    assert "SAM_DEEPSEEK_API_KEY" not in values and "SAM_TELEGRAM_CHAT_ID" not in values
    assert not result.activated and "not activated yet" in result.activation and "Sam" not in active_users(params)
    signup._print_result(result, answer(SAM), dry_run=False)
    printed = capsys.readouterr().out
    assert "MISSING  SAM_GEMINI_API_KEY" in printed and "secret-gemini-value" not in printed

    # Keys arrive: Andres replaces the placeholders, and the next check finds nothing missing
    env.write_bytes(env.read_bytes().replace(b"SAM_ANTHROPIC_API_KEY=None", b"SAM_ANTHROPIC_API_KEY=sk-ant-x")
                    .replace(b"SAM_GEMINI_API_KEY=None", b"SAM_GEMINI_API_KEY=AIza-x") + b"SAM_TELEGRAM_CHAT_ID=123\r\n")
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
    assert "STEVEN_HOLDINGS_LLM_MODEL=\r\n" in raw  # The default model: the earlier choice is cleared
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
    assert any("Kim is now active" in body for body in sent)
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
    from src.check_config import _env
    from src.personalized.user_profiles import env_setting
    for value, expected in (("None", None), ("  ", None), ("sk-real", "sk-real"), (" AIza-x ", "AIza-x")):
        monkeypatch.setenv("X_GEMINI_API_KEY", value)
        assert env_setting("X_GEMINI_API_KEY") == expected and _env("X_GEMINI_API_KEY") == expected
    lee = signup.plan_from_answer(answer(LEE))
    rows = signup.missing_report("Lee", {"LEE_HOLDINGS_SOURCE": "SHEETS", "LEE_HOLDINGS_LLM_MODEL": "deepseek-v4-pro",
                                         "LEE_DEEPSEEK_API_KEY": "None"}, lee)
    assert ("MISSING", "LEE_DEEPSEEK_API_KEY") in [(status, key) for status, key, _ in rows]
    assert ("MISSING", "LEE_ANTHROPIC_API_KEY") not in [(status, key) for status, key, _ in rows]


def test_a_deepseek_user_gets_placeholders_for_the_deepseek_and_gemini_keys_only(files):
    env, params = files
    signup.apply_answer(answer(LEE), env, params)
    values = dotenv_values(env)
    assert values["LEE_DEEPSEEK_API_KEY"] == values["LEE_GEMINI_API_KEY"] == "None"
    assert "LEE_ANTHROPIC_API_KEY" not in values
    before = env.read_bytes()
    signup.apply_answer(answer(LEE), env, params)  # Already there: not written again
    assert env.read_bytes() == before
