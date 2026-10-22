/**
 * Finnbot new-user sign-up form, built with Google Apps Script.
 *
 * How to use:
 *   1. Go to script.google.com and click "New project".
 *   2. Replace the contents of Code.gs with this whole file, and click Save.
 *   3. Choose createFinnbotSignupForm in the function menu at the top, and click Run.
 *   4. Approve the permissions it asks for (it creates a form, and a spreadsheet for the answers,
 *      in your own Google Drive).
 *   5. Open the execution log (View > Logs, or the log panel under the editor): it prints the
 *      form's edit link, the link to send to new users, and the answers spreadsheet's link.
 * Running it again creates a second, separate form; delete the old one from Drive if you do.
 *
 * How the form branches (each arrow is "go to that section"; a question is required only in the
 * sections a user actually reaches, so the other options' questions never block them):
 *
 *   1. Basics (everyone) ......... Holdings? -> SnapTrade | Google Sheets | Ideas only
 *   2. SnapTrade ................. -> 6. Your settings
 *   3. Google Sheets ............. How shared? -> privately: 4. Sheet ID | by link: 6. Your settings
 *   4. Sheet ID .................. -> 6. Your settings
 *   5. Ideas only ................ -> submit (no keys, nothing else needed)
 *   6. Your settings ............. -> 7. Telegram
 *   7. Telegram and watchdog ..... -> with watchdog: 8. Watchdog | otherwise: 9. Your keys
 *   8. Watchdog .................. -> 9. Your keys
 *   9. Your keys (confirmations) . -> submit
 *
 * The AI model isn't asked (2026-10-22): every user's holdings are reviewed by Claude Haiku 5.5 at
 * low effort (LLM["HOLDINGS"] in src/parameters.py), so everyone with holdings needs an Anthropic key.
 *
 * API keys, SnapTrade keys, and Telegram chat IDs are never asked for: the form's answers are
 * stored in a spreadsheet, so users send those separately through a private channel. Free-text
 * answers that look like an API key are rejected.
 *
 * Costs and options match docs/new_user_guide.txt (October 2026).
 */

const FORM_TITLE = 'Finnbot: new user sign-up';
const CREATE_RESPONSE_SHEET = true;  // Also create a spreadsheet that collects the answers

// Rejects free text containing something that looks like an API key (Anthropic and DeepSeek keys
// start with "sk-", Google keys with "AIza")
const KEY_PATTERN = 'sk-|AIza';
const KEY_WARNING = 'This looks like an API key. Never type keys into this form: send them to ' +
    'Andres privately instead (guide section 6.3).';

function createFinnbotSignupForm() {
  const form = FormApp.create(FORM_TITLE);
  form.setDescription(
      'Sign up for Finnbot\'s stock-market digest. It takes about 5-10 minutes. Please read ' +
      '"Finnbot: guide for new users" first: it explains every option below and what it costs.\n\n' +
      'BEFORE YOU START: if you\'re connecting holdings, create your API keys first (guide ' +
      'sections 3.6 and 6) and store them somewhere safe, such as a password manager.\n\n' +
      'DO NOT TYPE ANY API KEY, SNAPTRADE KEY, OR TELEGRAM CHAT ID INTO THIS FORM. Its answers are ' +
      'stored in a spreadsheet. Send those to Andres separately, one-to-one, through the private ' +
      'channel he gives you (for example an end-to-end encrypted messenger such as Signal), never ' +
      'by email, a group chat, or a shared document.');
  form.setProgressBar(true);
  form.setAllowResponseEdits(true);
  form.setConfirmationMessage(
      'Thanks! Andres will set you up. If you haven\'t yet, send your keys (and Telegram chat ID, ' +
      'if you use Telegram) to Andres privately, through the channel he gives you. Never send ' +
      'them by email, a group chat, or this form.');

  // ---------------------------------------------------------------- 1. Basics (everyone)
  form.addSectionHeaderItem()
      .setTitle('1. The basics')
      .setHelpText('Everyone answers these. Guide section 1.');
  form.addTextItem()
      .setTitle('Your first name')
      .setHelpText('Finnbot greets you with it ("Good morning, Sam"). Letters only; a hyphen is fine.')
      .setRequired(true)
      .setValidation(FormApp.createTextValidation()
          .setHelpText('Letters only (a hyphen is fine), up to 30 characters.')
          .requireTextMatchesPattern('^[A-Za-z][A-Za-z-]{0,29}$')
          .build());
  form.addTextItem()
      .setTitle('Email address for the digest')
      .setHelpText('A Gmail address read in the Gmail app works best.')
      .setRequired(true)
      .setValidation(FormApp.createTextValidation()
          .setHelpText('Please enter a valid email address.')
          .requireTextIsEmail()
          .build());
  form.addMultipleChoiceItem()
      .setTitle('Which digests do you want?')
      .setHelpText('Sent on trading days; ET is New York time. Each arrives about 20-30 minutes after ' +
                   'it starts. With holdings, two digests a day cost twice as much as one (guide 3.10).')
      .setRequired(true)
      .setChoiceValues([
        'Both: Pre-Market (starts 7:00 AM ET) and Post-Market (starts 4:10 PM ET)',
        'Pre-Market only (starts 7:00 AM ET, before the US market opens)',
        'Post-Market only (starts 4:10 PM ET, after the US market closes)',
        'Neither: Telegram alerts only (only works with holdings AND Telegram)',
      ]);
  const holdingsQuestion = form.addMultipleChoiceItem()
      .setTitle('How should Finnbot see your holdings?')
      .setHelpText('This decides which questions come next. Guide section 2.')
      .setRequired(true);

  // ---------------------------------------------------------------- 2. SnapTrade
  const snapPage = form.addPageBreakItem()
      .setTitle('2. SnapTrade')
      .setHelpText(
          'IMPORTANT: SnapTrade\'s own guidance says a Personal API key "must not be distributed in ' +
          'an app or shared with other people", and that it "may be able to trade where trading is ' +
          'enabled". Finnbot only reads your positions, but sending the key to anyone means trusting ' +
          'them with that access. Talk to Andres before choosing SnapTrade; Google Sheets avoids ' +
          'this entirely. Setup steps: guide section 2.1.');
  form.addCheckboxItem()
      .setTitle('Please confirm')
      .setRequired(true)
      .setChoiceValues([
        'I\'ve talked to Andres about using SnapTrade, and I\'ve read SnapTrade\'s warning above.',
        'I\'ve created a free SnapTrade Personal account with two-factor authentication, and ' +
            'connected my brokerage (read-only if offered).',
        'I\'ll send my SnapTrade Client ID and Consumer Key to Andres privately, NOT in this form.',
      ])
      .setValidation(FormApp.createCheckboxValidation()
          .setHelpText('Please tick all three before continuing.')
          .requireSelectAtLeast(3)
          .build());

  // ---------------------------------------------------------------- 3. Google Sheets
  const sheetsPage = form.addPageBreakItem()
      .setTitle('3. Google Sheets')
      .setHelpText('Your holdings go on the FIRST tab of a Google Sheet. Exact layout, symbols, and ' +
                   'examples: guide section 2.2.');
  form.addCheckboxItem()
      .setTitle('Please confirm your sheet\'s layout')
      .setRequired(true)
      .setChoiceValues([
        'Row 1 of the first tab has the column names symbol, shares_qty, avg_purchase_price ' +
            '(account_name and currency are optional), exactly as written.',
        'Symbols are written as Yahoo Finance writes them (e.g. AAPL, BRK-B, SHOP.TO, SHEL.L).',
        'Prices are plain numbers in each stock\'s own currency, with no $ or commas; London (.L) ' +
            'stocks are in pence, with the currency column left blank.',
      ])
      .setValidation(FormApp.createCheckboxValidation()
          .setHelpText('Please tick all three; the guide (section 2.2) shows how.')
          .requireSelectAtLeast(3)
          .build());
  const sharingQuestion = form.addMultipleChoiceItem()
      .setTitle('How did you share the sheet?')
      .setHelpText('Guide section 2.2, step 4.')
      .setRequired(true);

  // ---------------------------------------------------------------- 4. Sheet ID (private sheets)
  const sheetIdPage = form.addPageBreakItem()
      .setTitle('4. Your sheet\'s ID')
      .setHelpText('Because your sheet is shared only with Finnbot, its ID alone opens nothing, so it ' +
                   'can go here.');
  form.addTextItem()
      .setTitle('Sheet ID')
      .setHelpText('The long code in the sheet\'s web address, between "/d/" and "/edit". In ' +
                   'https://docs.google.com/spreadsheets/d/1AbCdEf.../edit it\'s 1AbCdEf...')
      .setRequired(true)
      .setValidation(FormApp.createTextValidation()
          .setHelpText('Paste only the ID: letters, numbers, - and _ (usually about 44 characters).')
          .requireTextMatchesPattern('^[A-Za-z0-9_-]{25,80}$')
          .build());

  // ---------------------------------------------------------------- 5. Ideas only
  const ideasPage = form.addPageBreakItem()
      .setTitle('5. Ideas only')
      .setHelpText('You get the market signals, macro news, and swing and long-term ideas, free. ' +
                   'No keys needed: this is the last question.');
  form.addTextItem()
      .setTitle('Your timezone (optional)')
      .setHelpText('Only sets whether the email says good morning, afternoon, or evening. A ' +
                   'city-based name, e.g. America/Toronto, Europe/London, Australia/Perth.')
      .setRequired(false)
      .setValidation(timezoneValidation_());

  // ---------------------------------------------------------------- 6. Your settings
  const settingsPage = form.addPageBreakItem()
      .setTitle('6. Your settings')
      .setHelpText('Guide section 3.');
  form.addTextItem()
      .setTitle('Brokerage name to show in your digest')
      .setHelpText('A label, e.g. "Questrade" or "My portfolio". Shown as "Questrade · 3 accounts".')
      .setRequired(true)
      .setValidation(FormApp.createTextValidation()
          .setHelpText(KEY_WARNING)
          .requireTextDoesNotContainPattern(KEY_PATTERN)
          .build());
  form.addTextItem()
      .setTitle('Your timezone')
      .setHelpText('A city-based name, e.g. America/Toronto, America/New_York, America/Bogota, ' +
                   'Europe/London, Australia/Perth, Australia/Sydney. Guide 3.2.')
      .setRequired(true)
      .setValidation(timezoneValidation_());
  form.addMultipleChoiceItem()
      .setTitle('Home currency')
      .setHelpText('Your portfolio\'s total, daily change, gain or loss, account values, and risk ' +
                   'notes are shown in it. Each stock\'s card stays in its own currency. Guide 3.3.')
      .setRequired(true)
      .setChoiceValues(['USD (default)', 'CAD', 'EUR', 'GBP', 'AUD', 'COP']);
  form.addMultipleChoiceItem()
      .setTitle('News for each holding')
      .setHelpText('Up to 2 short news summaries per stock, with sources, written by Gemini. Usually ' +
                   'the largest part of the bill. Your Gemini key is needed either way, for the ' +
                   'portfolio review. Guide 3.7.')
      .setRequired(true)
      .setChoiceValues([
        'On (default): about $0.012 per stock per digest',
        'Off: saves that cost',
      ]);

  // ---------------------------------------------------------------- 7. Telegram and watchdog
  const telegramPage = form.addPageBreakItem()
      .setTitle('7. Telegram and the watchdog')
      .setHelpText(
          'Telegram is a free messaging app. Finnbot\'s bot can send you urgent sell alerts (from the ' +
          'watchdog), earnings reminders 24 hours before your stocks report, and notices if your ' +
          'holdings can\'t be read. The watchdog re-checks your holdings during the day; it needs ' +
          'Telegram. Guide sections 3.8 and 3.9.\n\n' +
          'Your Telegram chat ID does NOT go in this form: send it to Andres privately with your keys.');
  const telegramQuestion = form.addMultipleChoiceItem()
      .setTitle('Do you want Telegram messages?')
      .setRequired(true);

  // ---------------------------------------------------------------- 8. Watchdog
  const watchdogPage = form.addPageBreakItem()
      .setTitle('8. The watchdog')
      .setHelpText(
          'Each check looks at prices first; only stocks that moved about a day\'s range, report ' +
          'earnings within 7 days, or weren\'t reviewed in 24 hours get a new AI review, and never more ' +
          'than 40 a day. On Claude Haiku 5.5 that\'s about $0.20-0.50 a month, at most about $1.70. ' +
          'Guide 3.9.');
  form.addTextItem()
      .setTitle('Hours between checks')
      .setHelpText('A whole number from 1 to 168. 4 is the default (about two checks per trading ' +
                   'day); 1 reacts fastest; 24 is once a day.')
      .setRequired(true)
      .setValidation(FormApp.createTextValidation()
          .setHelpText('A whole number from 1 to 168.')
          .requireTextMatchesPattern('^([1-9]|[1-9][0-9]|1[0-5][0-9]|16[0-8])$')
          .build());
  form.addMultipleChoiceItem()
      .setTitle('When should it run?')
      .setRequired(true)
      .setChoiceValues([
        'Regular (default): 9:30 AM-4:00 PM ET on trading days',
        'Extended: 4:00 AM-8:00 PM ET on trading days (pre-market and after-hours too)',
        'Around the clock: every hour, every day, weekends included (mostly adds checks)',
      ]);
  form.addMultipleChoiceItem()
      .setTitle('Tripwire')
      .setHelpText('A quick hourly price check between scheduled checks: a stock that moves about ' +
                   'twice its usual daily range gets reviewed right away. Costs nothing unless that ' +
                   'happens.')
      .setRequired(true)
      .setChoiceValues(['On (default)', 'Off']);

  // ---------------------------------------------------------------- 9. Your keys
  const keysPage = form.addPageBreakItem()
      .setTitle('9. Your keys')
      .setHelpText(
          'Last step. Your keys must be ready, but they are sent separately, never in this form. ' +
          'How to create them: guide section 3.6. How to store and send them safely: section 6.');
  form.addCheckboxItem()
      .setTitle('Please confirm')
      .setRequired(true)
      .setChoiceValues([
        'I\'ve created my Gemini API key and saved it somewhere safe (e.g. a password manager).',
        'I\'ve created my Anthropic API key and saved it somewhere safe.',
        'I have NOT typed any API key, SnapTrade key, or Telegram chat ID into this form.',
        'I\'ll send my keys (and my Telegram chat ID and SnapTrade keys, if any) to Andres ' +
            'one-to-one through the private channel he gives me, never by email, a group chat, ' +
            'a screenshot, or a shared document.',
      ])
      .setValidation(FormApp.createCheckboxValidation()
          .setHelpText('Please tick all four.')
          .requireSelectAtLeast(4)
          .build());
  form.addParagraphTextItem()
      .setTitle('Anything else Andres should know? (optional)')
      .setHelpText('No keys here, please.')
      .setRequired(false)
      .setValidation(FormApp.createParagraphTextValidation()
          .setHelpText(KEY_WARNING)
          .requireTextDoesNotContainPattern(KEY_PATTERN)
          .build());

  // ---------------------------------------------------------------- Branching
  // Set once every section exists, since a choice can only point at a section already in the form.
  holdingsQuestion.setChoices([
    holdingsQuestion.createChoice('Google Sheets: I\'ll keep my holdings in a spreadsheet', sheetsPage),
    holdingsQuestion.createChoice('SnapTrade: link my brokerage (talk to Andres first)', snapPage),
    holdingsQuestion.createChoice('Ideas only: no holdings, free', ideasPage),
  ]);
  sharingQuestion.setChoices([
    sharingQuestion.createChoice('Privately, with Finnbot\'s service-account email (recommended)', sheetIdPage),
    sharingQuestion.createChoice('By link ("Anyone with the link can view"): I\'ll send the sheet\'s ID ' +
                                 'to Andres privately, since anyone with it can see my holdings', settingsPage),
  ]);
  telegramQuestion.setChoices([
    telegramQuestion.createChoice('Yes, with the watchdog: urgent sell alerts, earnings reminders, and ' +
                                  'notices', watchdogPage),
    telegramQuestion.createChoice('Yes, without the watchdog: earnings reminders and notices only', keysPage),
    telegramQuestion.createChoice('No Telegram', keysPage),
  ]);

  // Where each section goes when it ends. A page break's "go to" applies to the section just before
  // it, so these are set on the next section in the form's order.
  sheetsPage.setGoToPage(settingsPage);                        // After 2. SnapTrade
  ideasPage.setGoToPage(settingsPage);                         // After 4. Sheet ID
  settingsPage.setGoToPage(FormApp.PageNavigationType.SUBMIT);  // After 5. Ideas only: done
  // 6. Your settings continues to 7, and 8. Watchdog to 9, in order; 9 is the last section.

  // ---------------------------------------------------------------- Answers spreadsheet and links
  let sheetUrl = '(not created)';
  if (CREATE_RESPONSE_SHEET) {
    const sheet = SpreadsheetApp.create(FORM_TITLE + ' (answers)');
    form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());
    sheetUrl = sheet.getUrl();
  }
  // Newer Google Forms need a form published before it accepts answers; older ones are published
  // from the start
  if (typeof form.setPublished === 'function') {
    form.setPublished(true);
  }
  Logger.log('Edit the form:            ' + form.getEditUrl());
  Logger.log('Send this to new users:   ' + form.getPublishedUrl());
  Logger.log('Answers spreadsheet:      ' + sheetUrl);
}

/** A city-based timezone name such as America/Toronto or America/Argentina/Buenos_Aires. */
function timezoneValidation_() {
  return FormApp.createTextValidation()
      .setHelpText('A city-based timezone name, e.g. America/Toronto or Europe/London.')
      .requireTextMatchesPattern('^[A-Za-z]+(/[A-Za-z0-9_+-]+)+$')
      .build();
}
