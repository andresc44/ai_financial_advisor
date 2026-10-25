# Finnbot: setup instructions

The accounts and keys to get ready before you join, in order. About 30–45 minutes.
For what each choice means and costs, see the full guide ([new_user_guide.txt](new_user_guide.txt)). October 2026.

## Which steps are yours

| You want | Steps |
|---|---|
| Ideas only (no holdings) | 1, 6, 7 |
| Holdings in a Google Sheet | 1–3, 5–8 (skip SnapTrade) |
| Holdings linked through SnapTrade | Every step. Talk to Andres before step 4. |

Telegram (step 3) is optional, but without it you get no alerts between emails.

## 1. Store everything safely, as you go

Every login, key, ID, and recovery code below goes in a password manager (for example Bitwarden, 1Password, or your
phone's built-in one) the moment you see it. Some are shown only once, and pages and messages can disappear, so don't
rely on finding them again later. Never keep keys in notes, screenshots, email drafts, or chats.

## 2. Anthropic (Claude) API key: reviews your holdings

1. Go to <https://platform.claude.com/dashboard> and create an account. This is separate from a Claude chat
   subscription; a Pro or Max plan doesn't include it.
2. In **Billing**, add a card and buy at least **$5 USD** of credit (that lasts months for most users).
   Recommended: set a monthly spend limit too.
3. In **API keys**, click **Create key**, name it `Finnbot`, and copy the key (it starts with `sk-ant-`).
   It's shown only once: save it now (step 1).

## 3. Telegram: alerts on your phone

1. Install Telegram on your phone and create an account.
2. Search for **@Finn31_bot** (Finn), open it, and press **Start**. It can't message you until you do.
3. Get your chat ID: message **@userinfobot**; it replies with a number like `123456789`. Save it.

## 4. SnapTrade: only if you link your brokerage

1. Sign up at <https://dashboard.snaptrade.com/signup> (the Personal plan is free).
2. Turn on two-factor authentication (2FA). SnapTrade requires it for API keys.
3. Connect your brokerage and log in to it in SnapTrade's window. Choose read-only if offered: Finnbot never trades.
4. Open the API key page and create a key. You get a **Client ID** and a **Consumer Key**. Save both; the Consumer Key
   works like a password to your brokerage data.
5. Save SnapTrade's recovery codes too, if it gives you any.

## 5. Gemini API key: news for each holding

Only needed if you want news for each holding.

1. Go to <https://aistudio.google.com/api-keys> and sign in with a Google account.
2. Click **Create API key** (let it create a project if asked) and copy the key (it starts with `AIza`). Save it.

It's free to start with. If your holdings' news is often missing or late, Andres may ask you to turn on billing for the
key's project (about $0.55 a month for 10 holdings).

## 6. Gmail: so the email's dropdowns work

1. On a computer, open Gmail in a browser (mail.google.com), in the account the digest goes to.
2. Click the gear icon, then **See all settings**. On the **General** tab, find **Dynamic email** and tick
   **Enable dynamic email**.
3. Click **Developer settings**, add `finnbot107@gmail.com`, and click **OK**.
4. Scroll down and click **Save Changes**.
5. Install the Gmail app on your phone and sign in to the same account. The digest is designed for reading there.

## 7. Fill in the sign-up form

Andres sends you the link. It asks for your name, email, which digests you want, and your choices.
**Never put a key in the form**: its answers are stored in a spreadsheet.

## 8. Send your keys to Andres, privately

Send only what applies to you:

- [ ] Anthropic key (step 2)
- [ ] Telegram chat ID (step 3)
- [ ] SnapTrade Client ID and Consumer Key (step 4, only after agreeing it with Andres)
- [ ] Gemini key (step 5)
- [ ] Google Sheet ID, if your sheet is shared by link

**How:** one-to-one, through the private channel Andres gives you (for example Signal). Never by ordinary email or
text, in a group chat, a screenshot, or a shared document. Delete the message on your side once Andres confirms he has
it. Nobody else will ever ask for your keys on Finnbot's behalf; if someone does, don't send them.

## What happens next

Once your answers and keys are in, you're switched on at the next hourly check, and Andres gets a message. Your first
digest arrives with the next one due: 7:00 AM or 4:10 PM New York time on trading days, about 20–30 minutes after it
starts. If a key stops working later, the top of your email says so, and the rest still arrives.
