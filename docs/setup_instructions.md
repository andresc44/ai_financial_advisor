# Finnbot: setup instructions

The accounts and keys to get ready before you join, in order. About 30–45 minutes.
For what each choice means and costs, see the full guide ([new_user_guide.txt](new_user_guide.txt)). October 2026.

## Which steps are yours

| You want | Steps |
|---|---|
| Ideas only (no holdings) | 1, 7, 8 |
| Holdings set manually in a Google Sheet | 1–3, 5–9 (skip SnapTrade, step 4) |
| Holdings linked automatically (through SnapTrade) | Every step except 5.|

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

## 3. Telegram: If you'd like important alerts on your phone (Optional)

1. Install Telegram on your phone and create an account.
2. Search for **@Finn31_bot** (Finn), open it, and press **Start**. It can't message you until you do.
3. Get your chat ID: message **@userinfobot**; it replies with a number like `123456789`. Save it.

## 4. SnapTrade: only if you link your brokerage

1. Sign up at <https://dashboard.snaptrade.com/signup> (the Personal plan is free).
2. Turn on two-factor authentication (2FA). SnapTrade requires it for API keys.
3. Connect your brokerage and log in to it in SnapTrade's window. Choose read-only if offered: Finnbot never trades.
4. Open the API key page and create a key. You get a **Client ID** and a **Consumer Key**. Save both; the Consumer Key
   works like a password to your brokerage data.
5. Save SnapTrade's recovery codes too, if it gives you any. Not needed for Finnbot setup.

## 5. Google Sheets: only if you track your holdings manually

Skip this if you're using SnapTrade or ideas only.

1. Create a new Google Sheet (sheets.google.com, then **Blank**).
2. On the first tab, type these column names in row 1, exactly as written (lowercase, with underscores), then one
   row per position starting in row 2:

   | Column | Required? | What goes in it |
   |---|---|---|
   | `symbol` | Yes | The stock's symbol as Yahoo Finance writes it, e.g. `AAPL`, `SHOP.TO` |
   | `shares_qty` | Yes | How many shares you own (fractions are fine) |
   | `avg_purchase_price` | Yes | Your average price per share, in the currency the stock trades in |
   | `account_name` | No | Which account it's in, e.g. TFSA, RRSP, Margin |
   | `currency` | No | Optional: Finnbot detects it by default |

   Any other column (for example "notes") is ignored.
3. Share the sheet so Finnbot can read it. Either:
   - **Private (recommended):** click **Share**, paste Finnbot's service-account email
     (`finnbot-service@finnbot-504921.iam.gserviceaccount.com`), set it to **Viewer**, untick **Notify people**, and
     click **Share**. Only you and Finnbot can see the sheet. 

4. Find the sheet's ID: the long code in its web address, between `/d/` and `/edit`. For example, in
   `https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=0` the ID is
   `1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789`. The ID alone opens nothing, but is required for Google Sheets setup.

For symbol formats on other exchanges, how to enter prices (including London stocks, priced in pence), and a full
worked example, see [new_user_guide.txt, section 2.2](new_user_guide.txt).

## 6. Gemini API key: news for each holding

Only needed if you want news for each holding.

1. Go to <https://aistudio.google.com/api-keys> and sign in with a Google account.
2. Click **Create API key** (let it create a project if asked) and copy the key (it starts with `AIza`). Save it.

It's free to start with. If your holdings' news is often missing or late, Andres may ask you to turn on billing for the
key's project (about $0.50 a month for 10 holdings, $1.05 for 20, with both digests).

## 7. Gmail: so the email's dropdowns work

1. On a desktop computer (or desktop view on mobile phone), open Gmail in a browser (mail.google.com), in the account you want the digest goes to (has to be sent to a Gmail account).
2. Click the gear icon, then **See all settings**. On the **General** tab, find **Dynamic email** and tick
   **Enable dynamic email**.
3. Click **Developer settings**, add `finnbot107@gmail.com`, and click **OK**.
4. Scroll down and click **Save Changes**.
5. Install the Gmail app on your phone and sign in to the same account. The digest is designed for reading there.

## 8. Fill in the sign-up form

Andres sends you the link. It asks for your name, email, which digests you want, and your choices.
**Never put a key in the form**: its answers are stored in a spreadsheet.

## 9. Send your keys to Andres, privately

Send only what applies to you:

- [ ] Anthropic key (step 2)
- [ ] Telegram chat ID (step 3)
- [ ] SnapTrade Client ID and Consumer Key (step 4)
- [ ] Google Sheet ID (step 5)
- [ ] Gemini key (step 6)

**How:** one-to-one, through the private channel Andres gives you. Never by ordinary email or
text, in a group chat, a screenshot, or a shared document. Delete the message on your side once Andres confirms he has
it. Nobody else will ever ask for your keys on Finnbot's behalf; if someone does, don't send them.

## What happens next

Once your answers and keys are in, you're switched on at the next hourly check, and Andres gets a message. Your first
digest arrives with the next one due: 7:00 AM or 4:10 PM New York time on trading days, about 20–30 minutes after it
starts. If a key stops working later, the top of your email says so, and the rest still arrives.
