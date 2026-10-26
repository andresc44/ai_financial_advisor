# Choosing Finnbot's AI models (2026-10-22; news model added 2026-10-26)

Finnbot asks an AI model to score new-stock ideas (long-term and swing) and to review each user's holdings. This
document explains how the models were compared on 2026-10-22, what the results were, and which settings were chosen.

## Summary

| Job | Setting chosen | Why |
|---|---|---|
| Long-term and swing ideas | **DeepSeek Flash 4.1, high effort** (`LLM.UNIVERSAL`: `deepseek-flash`, `high`) | Ranks stocks almost as well as the best Claude models for under a tenth of the price: about $0.23 a run, ~$10 a month |
| Long-term picks | Pass mark **85**, **3** picks, at least **1 outside tech** (which may score from 80), at most **2 tech** (2026-10-23) | Flash 4.1 scores long-term ideas ~2 points above the reference, so 85 plays the role 83 would for it; the pick count and the non-tech rule are Andres's choices |
| Swing picks | Pass mark **75** (set earlier the same day) | — |
| Holdings | **Claude Haiku 5.5, low effort** (`LLM.HOLDINGS`), for every user, with a clearer rubric worked from facts computed in code | With the new rubric it gives Opus's label for 93% of holdings, for about $0.05 a run against Opus's $2.04, and keeps users' data with Anthropic |
| News (holdings and macro) | **Gemini 3.5 Flash-Lite with Google Search** (`GEMINI_MODELS`, 2026-10-26) | About half the cost of `gemini-3.6-flash` (a quarter once its price doubles on 2027-01-01), with news close to it; no free tier option (see [the news model](#the-news-model-2026-10-26)) |

## What was tested

**Identical inputs.** A live Pre-Market digest build for Andres and Steven was run with every AI call replaced by a
recorder, so every model scored exactly the same prompts:

- 90 long-term candidates and 30 swing candidates (the swing screen's facts and trade plans included);
- 29 holdings: Andres's 10 and Steven's 19.

**Ten settings:**

| Provider | Model | Efforts tried |
|---|---|---|
| DeepSeek | Flash 4.1 (`deepseek-flash`) | none (no thinking), low, "medium", high |
| DeepSeek | V4 Pro (`deepseek-v4-pro`) | low |
| Anthropic | Claude Haiku 5.5 | low, medium |
| Anthropic | Claude Sonnet 5.5 | low, medium |
| Anthropic | Claude Opus 5.5 | medium (the reference) |

`deepseek-flash` is served by DeepSeek-V4.1-Flash-1017 (checked with DeepSeek's model list; Finnbot was already on
it). DeepSeek's effort levels are low, high and max: "medium" is accepted but behaved exactly like high.

**Repeats.** Opus ran 3 times. Flash 4.1 (none, low, high) and Haiku ran 3 times on the ideas and twice on the
holdings; the other settings ran twice. Repeats show how much each model disagrees with itself.

**One call per answer.** Finnbot's second looks (re-scoring close calls, re-reviewing sell signals) were switched off,
so each answer is a single model call.

**Costs** are at list prices (`API_PRICES`), with every input token at the uncached price and DeepSeek at its off-peak
price, when the digest runs. The experiment itself cost $43.87 (Anthropic $39.99, DeepSeek $3.88).

## How the results were compared

**The reference is Claude Opus 5.5 at medium effort**, the strongest model available. It is a reference, not the truth:
the question is how close each cheaper setting gets to Opus, and at what cost. Opus's own runs compared with each other
show the best agreement any model can be expected to reach.

Every comparison is pairwise: each run of a model against each Opus run. Rankings and picks are compared with the
average of two Opus runs, and Opus's own runs are compared the same way.

| Measure | Meaning |
|---|---|
| Score gap / bias | Average points away from Opus; bias keeps the sign (+ = scores higher than Opus) |
| Ranking (rank correlation) | How alike the whole list is ordered (1 = the same order) |
| Pass/fail accuracy at 70 or 60 | Every stock at or above the mark, compared with Opus's set; one figure combining the share of Opus's stocks found and the share of its own stocks Opus agrees with |
| Same-size overlap | Ranking only: if Opus passes K stocks, how many of the model's own top K are among them (ignores a model scoring everything higher or lower) |
| Picks | Finnbot's actual picks (top 2 long-term and top 5 swing at the time, by its sector rules), compared with Opus's |
| Holdings | The share of holdings given the same label as Opus; how many steps more or less cautious (buy more → hold → profit sell → risky → urgent sell); whether Opus's sell calls are caught; false sell calls |

## Results: long-term and swing ideas

| Setting | Ideas cost / run | Long-term ranking vs Opus | Long-term pass/fail at 70 | Long-term bias | Swing pass/fail at 70 | Swing pass/fail at 60 | Swing bias |
|---|---|---|---|---|---|---|---|
| Flash 4.1 none | $0.14 | 0.84 | 90% | +0.3 | 79% | 78% | +3.6 |
| Flash 4.1 low | $0.20 | 0.88 | 89% | +2.6 | 86% | 87% | +1.5 |
| Flash 4.1 "medium" | $0.23 | 0.90 | 89% | +1.9 | 88% | 90% | +1.1 |
| **Flash 4.1 high** | **$0.23** | **0.89** | **89%** | **+2.0** | **100%** | **86%** | **+1.8** |
| DeepSeek Pro low | $0.93 | 0.82 | 86% | +0.9 | 83% | 80% | −0.6 |
| Haiku 5.5 low | $0.17 | 0.82 | 70% | −6.0 | 84% | 80% | +2.2 |
| Haiku 5.5 medium | $0.18 | 0.88 | 75% | −5.0 | 100% | 79% | +1.7 |
| Sonnet 5.5 low | $2.59 | 0.89 | 90% | −0.3 | 100% | 81% | +2.7 |
| Sonnet 5.5 medium | $2.59 | 0.89 | 90% | −0.2 | 90% | 84% | +2.8 |
| *Opus 5.5 medium (vs itself)* | *$6.06* | *0.98* | *94%* | *0.0* | *100%* | *93%* | *+0.1* |

What this shows:

- **Ranking is close across most models.** Flash 4.1 with thinking, Haiku medium and Sonnet all order the long-term list
  about equally well (0.88–0.90). They differ mainly in score level: Haiku scores long-term ideas 5–6 points below Opus,
  Flash 4.1 about 2 points above, Sonnet about the same.
- **Swing:** Flash 4.1 high, Haiku medium and Sonnet low passed exactly Opus's stocks at 70 in every run. At 60, Flash
  4.1 with thinking came closest (86–90%), as Sonnet and Haiku pass more stocks Opus scores in the 50s.
- **DeepSeek V4 Pro** costs four times as much as Flash 4.1 and does no better, and it is the slowest.

### Flash 4.1 effort levels

| Effort | Ideas cost / run | ≈ per month (44 runs) | Run time (long-term, swing) | Long-term ranking | Order of the top long-term stocks | Swing pass/fail at 70 | Extra swing picks at the 75 mark |
|---|---|---|---|---|---|---|---|
| None | $0.14 | ~$6 | 26 s, 9 s | 0.84 | 0.61 | 79% | 1.7 per run |
| Low | $0.20 | ~$9 | 55 s, 46 s | 0.88 | 0.73 | 86% | 0.7 per run |
| **High** | **$0.23** | **~$10** | **67 s, 50 s** | **0.89** | **0.76** | **100%** | **0** |

No thinking saves about $3 a month against low but is clearly worse: it orders the top long-term stocks poorly and adds
weak swing picks. High costs about $1.50 a month more than low and was the best of the three on nearly every measure.

## Results: holdings (29 positions)

| Setting | Holdings cost / run | Same label as Opus | Bias (steps; + = more cautious) | Opus's sell calls caught | False sell calls / run | Agrees with itself |
|---|---|---|---|---|---|---|
| Flash 4.1 none | $0.050 | 71% | +0.28 | 67% | 1.0 | 83% |
| Flash 4.1 low | $0.062 | 71% | +0.48 | 100% | 0.5 | 83% |
| Flash 4.1 "medium" | $0.073 | 72% | +0.53 | 100% | 0.5 | 93% |
| Flash 4.1 high | $0.071 | 72% | +0.41 | 100% | 1.5 | 76% |
| DeepSeek Pro low | $0.280 | 71% | +0.48 | 100% | 1.0 | 79% |
| Haiku 5.5 low | $0.051 | 68% | +0.57 | 100% | 0.5 | 86% |
| Haiku 5.5 medium | $0.053 | 67% | +0.52 | 100% | 1.0 | 83% |
| Sonnet 5.5 low | $0.884 | 71% | +0.36 | 50% | 0.0 | 86% |
| Sonnet 5.5 medium | $0.886 | 67% | +0.47 | 50% | 0.0 | 97% |
| *Opus 5.5 medium (vs itself)* | *$2.044* | *95%* | *−0.09* | *100%* | *0.0* | *93%* |

- Every model other than Opus gives the same label as Opus for about 7 holdings in 10, whatever its price or effort.
- All of them are more cautious than Opus: about 10 "risky" labels per run against Opus's 5, and almost never "buy more"
  (Opus gave 3).
- Where they differ is often a judgement call rather than an error. Opus called two 3× leveraged ETFs "hold" and two
  holdings "buy more"; most other models called one of those ETFs "risky" and the two "hold" or "risky".
- On sell calls, which drive alerts, the DeepSeek and Haiku settings caught all of Opus's; Sonnet caught half.

### A clearer holdings rubric

The gap was in the instructions more than the models. Of the extra "risky" labels the cheaper models gave where Opus
held or bought, 64% cited the price being below its short-term averages and 56% an upcoming report: questions the old
rubric left to judgment ("no sign of a floor"). The fix copies what made the swing scores consistent:

- **Facts worked out in code** for each holding (`src/personalized/holding_checks.py`): the long-term trend, a pullback
  from the 20-day high, a run from the 20-day low, the four signs of a turn (the swing rubric's), whether the price is
  below EMA 21 or under its trend stop, whether it is stretched, trading days to the next report, and whether it is a
  fund or a leveraged ETF.
- **Labels checked in a fixed order** with stated thresholds: urgent sell, profit sell (a real recent run and two
  signs it is tiring), risky (one serious warning or two lesser ones), buy more (a pullback in an intact trend with two
  signs of a turn), and hold for everything else.
- **Andres's choices:** "risky" means genuinely risky and worth a close eye; funds, including leveraged ETFs, are
  judged on their trend; an upcoming report is a lesser warning, so it needs another one.

Re-run on the same 29 holdings (Opus once, the others twice; DeepSeek Pro and Sonnet left out):

| Setting | Same label as Opus, old rubric | Same label as Opus, new rubric | Bias vs Opus, new rubric (steps) | Agrees with itself, new rubric |
|---|---|---|---|---|
| Flash 4.1 none | 71% | 62% | −0.05 | 79% |
| Flash 4.1 low | 71% | **93%** | −0.07 | 100% |
| Flash 4.1 high | 72% | **95%** | −0.05 | 97% |
| Haiku 5.5 low | 68% | **93%** | −0.09 | 86% |
| Haiku 5.5 medium | 67% | **91%** | −0.07 | 83% |

- With thinking on, the cheaper models and Opus now agree on 91–95% of labels, with no lean either way. Without
  thinking, Flash 4.1 can't follow the steps (62%).
- Opus itself changed: the rules, not the model, now set the level of caution. With an upcoming report still counting
  on its own, every model called about 9 of the 29 holdings risky. Once it needed another warning, Haiku 5.5 low called
  5 risky per run, Opus's old level, the same 5 in both runs; the 4 flagged mainly for their upcoming reports went
  back to hold.
- The one remaining difference is profit sells: Opus kept two holdings as profit sells where the Flash settings found
  only one sign the run is tiring.

## Decisions

1. **Ideas run on DeepSeek Flash 4.1 at high effort.** Among settings that rank close to Opus, it is the cheapest by a
   wide margin, and on swing it matched Opus exactly at 70. Sonnet 5.5 ranks long-term ideas slightly better but costs
   about 11 times as much.
2. **Long-term pass mark 85** (was 80). Flash 4.1 high scores long-term ideas about 2 points above Opus: at 80, 19
   stocks cleared it against Opus's 9; at 85, 7 do.
3. **Three long-term picks, at least one outside tech** (Andres's rules). Tech means Yahoo's Technology and
   Communication Services sectors (`TECH_SECTORS`), so internet and ad-tech names (GOOGL, META, RDDT, APP) count as
   tech. The long-term picks were already from different sectors, so with Technology alone the rule would never change
   a pick. The non-tech pick may score from 80 (`LONG_TERM_NON_TECH_MIN_SCORE`): it joins the picks when there is
   room, otherwise it replaces the lowest-scoring tech pick.
4. **Swing pass mark 75**, unchanged.
5. **Holdings default to Claude Haiku 5.5 at low effort**, with the new rubric. With it, any model with thinking on
   matched Opus closely, so the choice came down to cost and where users' data goes: Haiku keeps it with Anthropic.
   It applies to every user: the sign-up form no longer asks for a model, and the old per-user settings are ignored
   (so everyone with holdings needs an Anthropic key).
6. **The portfolio review uses the holdings model too** (2026-10-23, to cut Gemini costs). It was on Gemini, which
   charged mostly for thinking; Haiku 5.5 at low effort writes it with the key every user already has. This wasn't
   part of the comparison above; details in `docs/watchdog.md` (Holding news and the portfolio review).

### Why the non-tech pick may score from 80

On 2026-10-22's candidates, with Flash 4.1 high, about 7 stocks reached 85, nearly all chipmakers, and the best
non-tech stocks scored 82–84 (VEEV, NVT, NBIX, VRT, STT). With different sectors required, that left 1–2 long-term
picks, all tech, and a non-tech pick held to 85 could not be found. Andres set its mark at 80, so on that day one of
those stocks would have joined the picks.

## The news model (2026-10-26)

Holding news and macro news use Gemini with Google Search. On 2026-10-26 four settings were compared with Finnbot's real news code (`fetch_symbol_news`, `fetch_live_macro_stories`), swapping only the model, on Andres's 11 holdings (3 per request) and the macro news, 2 runs each, plus one 20-holding run on the free model. Nothing was emailed or recorded.

| | `gemini-3.6-flash` (was the default) | `gemini-robotics-er-2-preview` (free key) | **`gemini-3.5-flash-lite`** | `gemini-3.1-flash-lite` |
|---|---|---|---|---|
| Prices per million tokens (in / out) | $0.75 / $3.75, then $1.50 / $7.50 from 2027-01-01 | $0 on a free key | $0.30 / $2.50 | $0.25 / $1.50 |
| Holdings news, cost per run | $0.0175 ($0.0350 from 2027) | $0 | **$0.0087** | $0.0134 |
| Macro news, cost per digest | $0.0042 | $0 | $0.0026 | $0.0023 |
| Thinking tokens per holdings run | 836 | 4,347 | **0** | 5,058 |
| Seconds per holdings run | 12 | 17 | **8** | 14 |
| Stocks with no news, of 11 | 1.5 | 3.0 | 2.0 | 3.5 |
| Links that resolved (holdings / macro) | 96% / 100% | 100% / 100% | 100% / 75% | 100% / 100% |
| Errors or limit hits | none | none (the 20-holding run: 28.7 s, 17 of 20 stocks with news) | none | none |

- **Chosen: 3.5 Flash-Lite.** It found most of the stories the baseline did, and some it missed (Agilent's earnings beat, NextEra's dividend), at about half the cost, because it doesn't think and thinking is billed as output. The weaknesses: one stale item (a story from Oct 6, outside the 7-day window) and 2 of its 4 macro links didn't resolve.
- **3.1 Flash-Lite** is cheaper per token, but thought about 5,000 tokens a run, so a run cost 54% more than 3.5's, and it had the most filler and the most empty stocks. One of its items (NextEra shareholders approving a merger with Dominion Energy) couldn't be confirmed.
- **The free robotics model** was the only one that searched on a free key, and left about twice as many stocks without news (real news existed for Agilent, AdaptHealth, and Viking). It's a preview model Google can withdraw.
- **The free tier has no search quota**: every grounded request on every other current model got `429 RESOURCE_EXHAUSTED` on a free key (the 2.0 and 2.5 models are gone or closed to new users; Gemma errored with an internal error; antigravity rejected the search tool), while the same requests worked on a billed key and plain requests worked on the free one.
- **Not different:** every model's answers varied between its own two runs (the baseline matched its own top story for 1 of 9 stocks), so run-to-run variation doesn't separate them. SPXL, a leveraged ETF, got no news from any model.
- **Quality scores** were judged by reading every stock side by side, not by a formula. Searches cost the same on every model ($14 per 1,000 beyond 5,000 a month per key, so none at Finnbot's volume).

Repeat it when Google changes the models or prices: the script was a working file and isn't in the repository.

## Caveats

- **One day of candidates.** Pick-level results hinge on a few close calls (TSM against NVDA, both chipmakers, 93.0
  against 90.5 for Opus); the ranking measures over all 90 and 30 stocks are steadier.
- **Opus is a reference, not the truth**, especially for holdings, where labels are judgement calls.
- **`deepseek-flash` is a moving name.** DeepSeek updated it on 2026-10-17 without changing the API name. After a
  model update, repeat the comparison.
- **Costs scale** with the number of candidates (the swing screen sends up to 40) and double during DeepSeek's peak
  hours.

## How to repeat the comparison

1. **Capture the inputs:** run a payload-only digest build with every AI call replaced by a recorder, and save the
   long-term and swing payloads and each user's holding payloads (with their market session).
2. **Score them with each setting:** run each model and effort over the saved inputs, at least twice (Opus three
   times), with the second looks off, and save every answer with its token usage and timing. A run cut short (out of
   credits, a spend limit) can be resumed for the missing items only.
3. **Analyse:** compare each run with each Opus run as described above, and price the tokens at list prices.

The scripts used on 2026-10-22 were working files and are not in the repository yet.
