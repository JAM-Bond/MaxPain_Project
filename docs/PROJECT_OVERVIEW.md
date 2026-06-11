# MaxPain Project — A High-Level Overview

*A personal options-trading research and decision-support system*

---

## How to read this document

This overview is written in two parts.

**Part I — How the System Works** (sections 1–6) is an explanatory text. It describes
what the system is for, how it chooses what to trade, and the reasoning behind each
layer of the design. It assumes no prior familiarity with the project and can be read
on its own as a description of the approach.

**Part II — Operating the System** (sections 7–14) is a user's guide. It describes the
daily rhythm of running the system, how to read what it produces, how to use the
dashboard, what a single trading cycle looks like from start to finish, and the routine
maintenance the system needs to keep running.

A single rule frames everything that follows, and it is worth stating before anything
else: **the system never places a trade.** It is advisory. It studies, it recommends,
and it scores discipline after the fact — but every order is entered by hand, by the
trader, on the trader's own judgment. Nothing in the software can buy or sell. Section 7
returns to this in detail; it is mentioned here so that nothing in Part I is misread as
an automated trading engine.

The precise, version-controlled mechanics — exact deltas, cohort membership, signal
formulas, and the revision log — live in the formal trading plan (`TRADING_PLAN.rtf`).
This document is the higher-level narrative and the how-to-operate layer; where the two
ever disagree, the trading plan is canonical.

---

# Part I — How the System Works

## 1. Purpose

The MaxPain Project is a personal options-trading system built to manage a credit-spread book across monthly options expiration cycles. It exists for one reason: to translate disciplined, evidence-based trading mechanics into a software harness that catches the small, repeated decision errors that compound into materially worse realized returns. The single-trader market participant rarely loses money on a single bad trade. They lose it through a hundred small drifts: closing winners early, holding losers past the rule, sizing too aggressively in adverse regimes, taking off-script trades when the framework says to wait. The system is built to make those drifts visible, then mechanical, then absent.

The project is not, and is explicitly not designed to be, a market predictor. It does not attempt to forecast where the broad market will trade next week, where any individual stock will land, or whether any specific position will win or lose. The edge — to the extent it exists — comes from the structural properties of pre-registered, walk-forward-validated trading patterns, applied consistently across many cycles. Nothing in the system is built to give the trader a clever read on tomorrow's tape.

What the project does provide, in order of operational priority:

A pre-registered playbook of validated trading structures, each backed by per-ticker historical evidence over years of options chain history. The playbook is sealed before live trading begins and modified only through the same pre-registration discipline that admitted the original structures.

A daily harness that, between four and five o'clock each afternoon Eastern time, generates a construction-block alert email summarizing actionable trades, regime state, position health, and a held-to-expiration counterfactual scoreboard for every position in the open book.

A dashboard surfacing live profit and loss against the rules — what every open position is worth, where each is relative to its loss-cap line, which positions have crossed the twenty-one-day management threshold, and which names in the cohort have validated per-ticker recommendations.

A post-mortem layer that, once a cycle closes, reads every artifact the system generated during that cycle through a sealed reasoning framework and produces an interpretive synthesis aimed at one question: did the trader execute the framework consistently, and where did discipline drift?

The system grew out of a recognition that a trader operating alone, without an institutional risk desk, needs a mechanical layer that holds the doctrine even when fear or emotion is most tempted to abandon it. The harness exists to be that layer.

---

## 2. Identifying Candidate Tickers

Candidate tickers are identified through historical backtesting against an archive of options-chain history covering many years of monthly cycles. The system does not select names by sector affiliation, market capitalization, news flow, or analyst sentiment. It selects names by demonstrated structural behavior under the specific trading patterns the playbook deploys.

The universe begins at one hundred fifty actively-traded equities and exchange-traded funds with sufficient options-chain depth to admit credit spreads with realistic execution. From this universe, separate cohorts are constructed for each trading structure: a bull-put cohort, a bear-call cohort, an inverted-fly cohort, and a ZEBRA (zero-extrinsic back-ratio) cohort split across two tier classifications. Each cohort is assembled through the same structured selection pipeline.

The two tier classifications in the ZEBRA cohort distinguish the original validated core from the subsequent expansion, and the same vocabulary is used informally for the other structures. Tier one is the founding cohort: the small set of highest-confidence names — the broad-market and technology indices and the largest-capitalization technology stocks — that passed the full selection pipeline at the structure's initial promotion and on which the foundational backtests, including the ZEBRA long-put-overlay study described in section five, were run. Tier two is the expansion cohort: additional names that subsequently cleared the identical selection gates and are promoted in by the automated auto-promotion scan as the evidence accumulates. Tier two therefore grows over time and is by now the larger of the two by a wide margin. Both tiers are fully tradeable and gated identically; the distinction records provenance and confidence — how long a name has carried validated evidence — rather than any difference in how a position is sized or managed.

The selection pipeline runs each candidate ticker through a backtest of the relevant trading structure across years of historical options data. The results are then split into two non-overlapping windows: a training window covering all monthly cycles through 2022, and a validation window covering 2023 onward. Within each window, the candidate's per-cycle profit-and-loss distribution is compared against alternatives using a paired non-parametric statistical test (the Wilcoxon signed-rank test). For a ticker to enter the cohort, three conditions must be met simultaneously: the test must reach significance at the standard threshold of five percent or better in both the training and validation windows; the direction of the winning alternative must agree across the two windows; and the validation window must contain at least twelve paired observations. A ticker that fits the pattern in the training data but reverses or fails to confirm in the held-out validation data is rejected, regardless of how strong the training evidence appears.

This methodology produces conservative cohort sizes. Of the original one hundred fifty universe names, only a fraction earn cohort membership for any given structure. Names that pass the screen carry a per-ticker recommendation: which moneyness or wing-width variant has historically performed best for that specific name. Names that do not pass continue to trade at the structure's universe-wide default settings.

The cohort tells the system which names are eligible. It does not tell the system when to trade them. That decision belongs to the gating layer described next.

---

## 3. Gates and Filters

Cohort membership identifies eligible tickers. Gates and filters determine, on each cycle's qualifier date, whether to actually deploy a position in any given cohort name and, if so, at what size.

The cycle qualifier runs each weekday morning. For every cohort name, it produces one of four verdicts: a full-size go, a half-size downsize, a paused skip, or a pending verdict awaiting more data. The verdict is the product of multiple gates running in sequence.

The bull-put gate examines the relationship between the underlying's current price and its two-hundred-day moving average. When the underlying is trading more than ten percent below its two-hundred-day moving average, the bull-put position is downsized to half its normal allocation. Historical study showed that bull puts entered when the underlying is materially below its long-term trend underperform sufficiently that full sizing is not warranted, even though they remain net-positive on average.

The bear-call gate is binary and macro-conditioned. Bear-call positions only fire when both conditions hold simultaneously: the broad-market index trades below its two-hundred-day moving average, and the broad-market implied-volatility rank exceeds 0.5. This gate is most often off. It rarely fires in sustained bull-market regimes, which is precisely the design intent. The historical evidence showed that bear-call structures generate dependable returns only in regime conditions of established weakness combined with elevated implied volatility.

The inverted-fly gate combines a spot-price ceiling at three hundred dollars with a term-structure-inversion filter. The spot ceiling reflects practical execution constraints; the term-structure filter looks for forward implied volatility lower than near-term implied volatility, a configuration that historically rewards long-volatility structures. When the term structure is in standard upward-sloping configuration, inverted-fly positions are held back.

The liquidity gate enforces a minimum credit-to-width ratio of 0.35. A spread that pays less than thirty-five cents of credit per dollar of width is rejected because the risk-reward arithmetic does not justify the position regardless of the cohort or signal. This gate is a guardrail against entering structurally unattractive trades when chain conditions degrade.

The earnings gate handles the special case of trading directional bias around scheduled earnings announcements. Names with established directional patterns through earnings windows enter at one or three days before announcement, with bias direction determined by the historical earnings-bias study.

A regime health monitor runs daily across three rings: an artificial-intelligence-stock concentration ring, a technology-index ring, and a broad-market ring. When two or more of the three rings flag red simultaneously, the system signals an early-warning exit cascade. The cascade is informational; the trader uses it to reassess the open book in light of regime stress.

Alongside the gating cascade, two purely descriptive early-warning reads surface in the daily alert. Neither gates, sizes, or votes in any cascade; both exist to make a deteriorating backdrop visible before it crosses a threshold. The first is a breadth ring that tracks the relative strength of the equal-weighted market index against the capitalization-weighted index. When the equal-weighted index keeps pace, breadth is broadening and the advance is high-quality; when the capitalization-weighted index pulls ahead, breadth is narrowing — the same nominal uptrend, but historically carrying roughly twice the downside tail — and narrowing while breadth is already extended is the narrow-megacap-top signature, the worst forward state in the underlying study.

The second is an overnight-drift watch. It decomposes each trading session for a small set of watched instruments — the semiconductor and technology indices and the broad-market index — into two legs: an intraday leg measured from the open to the close, and an overnight leg measured from the prior close to the next open. Comparing a trailing pattern window of roughly twenty-five sessions against the most recent handful, the watch reads three states. A balanced state, where intraday and overnight contributions are roughly even, is unremarkable. A levitation state, where gains are concentrated in the overnight session while the cash session is flat or weak, is a complacency or late-cycle tell — the "it always recovers by morning" conditioning that tends to accompany narrow, extended markets — though it is explicitly not a directional signal. A breaking state fires only where levitation had been established and the recent sessions show the overnight bid failing while intraday selling deepens; this is the pattern cracking. The breaking state is designed to be read together with the breadth ring: a narrowing or extended breadth ring combined with the overnight bid failing is the configuration in which an unwind is most likely to be confirming, but the read remains descriptive context for the trader rather than an instruction to act.

---

## 4. Moneyness

Moneyness — the relationship between the strike price selected and the underlying's current price — is one of the most consequential parameters in credit-spread construction. The system does not adopt a single moneyness convention across the entire book. It uses per-ticker walk-forward evidence to override defaults.

The universe-wide default short-strike delta for credit spreads is 0.30, a moderately out-of-the-money convention historically associated with high probability of finishing worthless. The walk-forward research tests three moneyness alternatives for each name: out-of-the-money at delta 0.30, at-the-money at delta 0.50, and in-the-money at delta 0.70. The same statistical methodology used for cohort selection — paired Wilcoxon test reaching significance in both training and validation windows, with directional agreement — determines whether a ticker earns a non-default recommendation.

For tickers that have earned a validated recommendation, the trade-construction code reads from a recommendation lookup file before each position open and applies the per-ticker moneyness rather than the default. Names without validated recommendations continue to trade at the universe-wide default until enough historical data accumulates to reach significance, or until a quarterly cohort refresh re-runs the validation pipeline against newly available cycles.

For inverted-fly structures, the analogous parameter is wing width: narrow at two percent of spot, medium at five percent, wide at ten percent, or extra-wide at fifteen percent. The same statistical pipeline tests each variant per ticker and produces validated recommendations.

A consequence of the validation discipline is that recommendations are narrow rather than universal. Approximately one in three universe names earns any per-ticker recommendation. The remainder trade at the structure default. This is a feature: the system commits to non-default settings only where the historical evidence is strong enough in both training and held-out data to justify the deviation.

---

## 5. Trading Rules

Once a position has cleared the cohort selection, the gating filters, and the moneyness assignment, the system applies a small set of execution rules. The rules are uniform across structures and are designed to be mechanical wherever possible.

Entries follow a standard timing window. Bull puts, bear calls, and inverted flies enter at forty-five days to expiration on the relevant monthly options-expiration cycle. ZEBRA structures enter at seventy-five days to expiration. Earnings-bias positions enter at one or three days before the announcement, depending on the per-ticker bias profile.

Every ZEBRA position is paired at entry with a long-put overlay sharing the same expiration as the parent structure and held to that expiration alongside the parent. The overlay is part of the ZEBRA construction itself rather than a discretionary management overlay. Backtest evidence across 934 cycles on the tier-1 cohort showed that the overlay produced positive cohort-mean expectancy lift in all four walk-forward validation windows, with the strongest lift in high-volatility single-name underlyings. Because the long-put overlay caps the tail-risk component that previously motivated a spot-price ceiling on ZEBRA candidates, the system no longer applies a spot-price filter to ZEBRA cohort eligibility. Any qualifying ticker is admitted; the trader retains discretion at trade-construction time on whether to deploy.

The overlay strike is selected by regime. A subsequent strike-grid sweep across seven points from ten percent in-the-money to twenty percent out-of-the-money revealed that pooled cohort means hide a regime-conditional pattern: in bull windows deeper out-of-the-money strikes dominate (cheap insurance, frequently expiring worthless), but in the 2022-2024 bear window the ranking inverts and in-the-money strikes deliver fifteen to twenty dollars per cycle of additional lift over the ten-percent-out-of-the-money default. A subsequent intra-bear stability test refined this further: the in-the-money advantage is concentrated in the active-drawdown phase of 2022. In the unwinding and recovery phases of 2023 and 2024 — still within the bear window in a high-level sense — out-of-the-money put strikes resumed outperforming, because the underlying was moving up off the trough and in-the-money puts decayed against that move.

The strike rule therefore anchors to the regime transition framework, the early-warning cascade, and an active-drawdown qualifier. In Stages One and Two with cascade rings green, the overlay is placed at ten to fifteen percent out-of-the-money — the inexpensive insurance that fits the calm-market regime. In Stage Three or when any cascade ring shows yellow, the overlay shifts to at-the-money, which is the only strike that delivered positive or zero lift in every walk-forward split. When the bear gate has opened — Stage Four or Five, or two cascade rings showing red — the strike depends on whether the bear is actively deepening or stabilizing. If SPY is making a new sixty-day low within the past thirty days, the overlay shifts to five percent in-the-money, capturing the deepening-bear lift documented in 2022. If the bear gate is open but SPY has not made a new sixty-day low in the past thirty days, the bear is troughing or unwinding and the overlay stays at ten percent out-of-the-money — the same strike used in calm regimes. Capital outlay per overlay rises roughly three- to four-fold across the out-of-the-money to in-the-money transition; sizing must adjust to hold the per-position budget within the ZEBRA capital-outlay allocation.

Both the parent ZEBRA and its long-put overlay are held to OpEx. The full Phase 2 sweep tested every plausible managed-exit variant — five separate exit criteria including time thresholds at T-thirty, T-twenty-one, and T-fourteen and profit targets at fifty and one hundred percent put gain — and all five variants underperformed held-to-expiration by two to six dollars per cycle with zero walk-forward splits showing positive lift. The credit-spread twenty-one-day discipline does not generalize to long-vol legs. Closing the put on any criterion forfeits the late-cycle gamma kick that is precisely what the long-vol overlay is purchased to capture. Three conditional-trigger variants — attach the put only on a five-percent drawdown, only on term-inversion at entry, only on breadth divergence at entry — were also tested and rejected. Trying to gate the overlay on a signal costs more than the saved premium; the always-on baseline is structurally robust. Strike selection at entry is the only regime-conditional parameter.

Sizing follows the qualifier verdict. A go verdict deploys at the standard size assigned to the cohort. A downsize verdict deploys at half size. A pause verdict skips the position entirely for the cycle. A pending verdict defers the decision and is treated as a pause until the next qualifier run resolves it.

A sector-concentration cap also operates at the verdict layer. No more than two single-name positions may be placed in the same GICS sector per options-expiration cycle. When more than two qualified candidates fall into the same sector for the same expiration, the qualifier ranks them by verdict tier with alphabetical tiebreaker and downgrades the third and lower to a concentration-driven skip. ETFs are exempt from the count because they are aggregate exposures by construction. The daily alert surfaces a sector-load warning on the second entry per sector so that placement is a conscious decision rather than silent stacking. The cap was triggered by a 2026-05-12 incident in which two financials in the same five-name cohort stopped together on a sector rotation: the cap is the structural answer to correlation clusters being misread as independent risk events.

Loss management is mechanical. Every credit spread, at the moment of entry, is paired with a good-til-canceled stop-limit buy-to-close order set at twice the entry credit. This enforces the doctrine that maximum realized loss must not exceed twice the target win, which is the structural complement to the standard fifty-percent profit-capture target. The stop is a mark-triggered, slightly higher limit-priced order so that activation under spread-debit expansion produces a fill rather than a market order, avoiding catastrophic slippage on wide spreads.

A natural question is whether this credit-based loss cap should be supplemented or replaced by a price-based stop — closing a spread the moment the underlying violates the short strike by some fixed depth. The system tested this directly across thirteen years and roughly twenty-seven thousand historical cycles, comparing a close-at-breach rule at two, five, and seven percent beyond the short strike against simply holding the position. The answer is that a symmetric price-breach stop does not help either structure and actively hurts bull puts. For bull puts the stop loses more the deeper the breach, and it loses worst precisely in downtrends, because violating the short strike in a falling, high-volatility tape means buying the spread back at an implied-volatility-inflated debit at the worst possible moment, only to watch the market V-bounce; recoveries beat the stop in nearly half of breached cycles and win larger when they win. For bear calls the rule is roughly a wash, turning marginally positive only in a confirmed downtrend and never enough to make the structure profitable in that regime. The conclusion is to add no fixed price-breach trigger; the credit-based two-times stop remains the sole mechanical loss control, and a breached spread is, by default, held rather than cut.

That universe-wide answer is then refined to the individual name. A per-ticker study walked every cohort name's full option-chain history through the same breach grid and measured, for each name and structure, both whether cutting at a given depth ever beats holding and how quickly a breached position tends to recover. The behavior is strongly direction-specific and is keyed on the ticker-and-structure pair. Bull puts almost universally mean-revert — sixty of sixty-seven cohort names — and once a robustness filter that demands sign-stability across separate training and validation windows is applied, only a single name survives as a genuine bull-put stop candidate. Bear calls break more often: ten names resist recovery, but only three survive the same walk-forward robustness filter as real upside-breakout stops at roughly three percent. Recovery, when it comes, is fast — a median of about two trading days — which quantifies the long-standing "wait a day" instinct the system has observed in shock-and-reversion episodes. This per-ticker breach profile is surfaced as a descriptive note on the relevant construction card: a mean-reverting name carries a reminder of how often its breaches recover and how quickly, advising the trader to hold through a transient violation, while one of the rare robust non-reverters carries an explicit suggestion to set a manual stop a stated depth beyond the short strike. The profile informs manual stop placement only; it neither sizes the position nor auto-places any order, and like the early-warning watches it would have to be formally pre-registered before it could gate anything.

Time-based exit is enforced by the twenty-one-day management cue. At twenty-one days remaining to expiration, the position is closed or rolled regardless of how much credit has been retained. The cue is rooted in the structural mathematics of options decay: at approximately twenty-one days remaining, the ratio between gamma exposure and theta accumulation flips against the credit-spread seller. Beyond that point, additional time spent in the position trades small additional decay benefit for materially expanded tail risk. Whatever profit has been captured by twenty-one days is the profit the trade earned.

Profit-target capture at fifty percent of credit collected remains as a secondary win-side reference, particularly for positions that decay quickly. Whichever exit fires first — fifty-percent capture, the twenty-one-day time cue, or the loss-cap stop — is the exit the trade takes.

All positions are defined-risk by construction. The system does not deploy uncovered short calls, uncovered short puts, or any structure whose maximum loss is not bounded at trade entry by the difference between the strike legs.

After each cycle closes, the system runs a held-to-expiration counterfactual against every closed position: what would the cycle's realized profit have been if every credit spread had been held to expiration? The delta between actual and held-to-expiration profit is the cycle's discipline scoreboard. A cycle in which the trader systematically exited too early shows a positive delta — profit left on the table. A cycle in which the trader correctly exited losing positions before they reached the loss cap shows a negative delta. Across many cycles, the trend in the delta becomes the most honest measure of whether the framework is being executed consistently.

---

## 6. Per-Name Macro-Sensitivity Profile

The cohort-selection, gating, and moneyness layers described above operate one ticker at a time. Each name's eligibility, sizing, and trade construction are evaluated against per-ticker historical evidence in isolation. This is appropriate for the trade-level decision but leaves a structural blind spot at the book level: two names that look like independent risks under the sector-concentration cap may in fact share an unobserved sensitivity to the same underlying economic factor. A bank and a homebuilder live in different GICS sectors but both fall together when long-term interest rates spike. A solar manufacturer and a real-estate investment trust likewise share a hidden coupling. The sector cap, designed to catch correlation among same-sector names, is silent on this class of cross-sector correlation.

The macro-sensitivity profile addresses that blind spot. For every name in the operational cohort union — approximately one hundred sixty-two tickers — the system measures the historical co-movement between the ticker's daily returns and each of eight underlying economic factors: changes in short-term and long-term interest rates, the slope of the yield curve, market-implied inflation expectations, the trade-weighted dollar, an equity-implied volatility index, the spread between high-grade and lower-grade corporate borrowing costs, and the broad stock market return itself. The measurement uses a rolling one-year window of daily observations against an option-chain-derived price spine reaching back to early 2013, and produces a time series of sensitivity coefficients for every (ticker, factor) pair on every trading day in the history. The market-return coefficient is included alongside the macro factors deliberately, so that each macro coefficient represents marginal sensitivity beyond the broad-market move rather than confounded co-movement.

A second analytical layer interrogates whether these measured sensitivities are stable across the distinct interest-rate regimes the period contains — the pre-2015 zero-rate era, the first hiking cycle through 2019, the COVID zero-rate return, the rapid 2022-2023 hiking cycle, and the higher-for-longer plateau extending into the current cutting environment. The finding of this stability analysis is the central result of the profile work and meaningfully constrains how the sensitivities should be used. For roughly eighty-three percent of names, the measured sensitivity to the broad market is stable across all five regimes — the historical coefficient is a reliable predictor of how the ticker will move when the market moves. For the same names, however, the measured sensitivities to interest rates, inflation expectations, and corporate-borrowing-cost changes reverse direction across regimes for one-third to one-half of the cohort. A bank stock that responded positively to rising long-term yields during the 2018 hiking cycle responded with the opposite sign during the regional-bank stress of 2023. The historical relationship is not a permanent property of the ticker; it is conditional on the broader environment.

The profile encodes this finding directly. Each ticker carries a sensitivity coefficient and a separate trust flag for each factor. The trust flag is true only when the historical relationship was either stable across all five regimes, or, in the case of magnitude-dependent relationships, was statistically significant in the current regime. For coefficients carrying a true trust flag, the value may be used as a quantitative input to sizing decisions. For coefficients carrying a false trust flag, only the categorical tier — strongly positive, mildly positive, neutral, mildly negative, strongly negative — is available, useful for directional context and diversification grouping but not for numerical scaling. The same treatment applies to the dollar, oil, and volatility coefficients, which are quantitatively too small to use for sizing but directionally consistent enough across regimes to drive a tiered diversification grouping. Approximately thirteen percent of names earn a true trust flag on the rate-sensitivity coefficient. Zero names earn the flag on the corporate-borrowing-cost coefficient, a statement that this particular factor produces too much regime-conditional noise to be quantitatively trusted for any name in the current period.

The profile feeds the live system at three integration points. The first is the daily construction-block alert: when more than one trade candidate clears the qualifier on a given evening, the alert checks whether any of the candidates share a non-neutral sensitivity tier on any of the seven dimensions. When two or more candidates share a tier — say, three candidates all in the strongly-positive rate-sensitivity tier — the alert appends a macro-concentration warning identifying the cluster. This is the cross-sector analog of the same-sector concentration cap and surfaces correlation that the sector cap cannot see. The warning is informational; it does not block placement, but it makes the cross-sector concentration visible at the moment trade decisions are made.

The second integration point is the post-mortem bundle generated after each options-expiration cycle closes. The bundle now contains a per-symbol macro signature table listing each closed trade's coefficient, tier, and trust flag across all seven dimensions, together with the current regime label. The post-mortem reasoning framework — which had previously been limited to narrative explanations such as a sector rotating against a position — can now reference quantitative context: a position that stopped on a day with an unusually large move in long-term inflation expectations can be evaluated against the ticker's historical sensitivity to that exact factor. Where the move was consistent with the historical sensitivity, the loss reads as regime risk realized on a well-understood exposure. Where the move was inconsistent or where the ticker carries a false trust flag on the relevant factor, the loss is uncategorized and remains an open question for further investigation.

The third integration point is the daily refresh discipline. The five-stage refresh pipeline — full-history economic-data ingest, price-spine extraction from the option-chain archive, the wide join, the rolling-window regressions, and the stability-tagged profile build — runs each weekday evening immediately after the daily option-chain refresh completes. The complete refresh takes approximately one minute. Each morning's daily alert and each newly generated post-mortem bundle reads the profile as of the previous trading day's close.

The profile, like every research layer in the system, is explicitly not a market predictor. It does not forecast future moves in interest rates, inflation expectations, the dollar, or any other factor. It describes how each name has historically responded to changes in those factors, with stability-conditional trust flags that prevent overconfident use of relationships that the historical data shows are regime-dependent. The benefit is mechanical rather than predictive: it surfaces cross-sector concentrations that would otherwise be invisible, attaches quantitative context to every loss the system records, and supplies the post-mortem layer with substrate it previously had to manufacture narratively.

---

# Part II — Operating the System

## 7. The Advisory Principle

The single most important fact about how this system is operated is that it never trades.
There is no order-placement code anywhere in the project. The system cannot buy, sell,
modify, or cancel a position by any means — not through the brokerage interface, not
through a script, not on a schedule, not as a "test." Its connection to the brokerage is
strictly read-only: it reads account balances, positions, and filled orders so that it
can keep its records honest, and that is the full extent of its reach into the account.

Every trade is placed by hand. The system's job is to do the homework — to say, on a
given afternoon, "these are the names that qualify, here is the structure and size the
rules call for, and here is where each open position stands against its exit lines." The
trader reads that, forms a judgment, and enters the orders manually at the brokerage.
The system is the analyst and the record-keeper; the trader is the only one with a
finger on the trigger.

This is a deliberate design choice, not a missing feature. A solo trader without an
institutional risk desk benefits from a mechanical layer that holds the doctrine
steadily — but the moment a system can act on its own conclusions, a software bug
becomes a financial loss rather than a bad recommendation that a human can decline. By
keeping the act of trading entirely in human hands, every recommendation passes through
a final layer of judgment, and no defect in the code can ever move money. "Going live,"
in this project's language, does not mean switching on an automated trader. It means the
trader decides to start acting on the recommendations with real capital instead of on
paper.

---

## 8. The Daily Operating Rhythm

The system runs on a fixed weekday schedule of scheduled jobs. The trader does not start
these by hand; they fire on their own, Monday through Friday, and deposit their results
where the trader can read them. The day has two pulses — a morning pulse that decides
what is eligible to trade, and an afternoon pulse that reports on the open book and
produces the day's action summary. Times below are U.S. Eastern.

**Early morning — health and housekeeping.** Before the market opens, the system checks
its own vital signs. A heartbeat monitor confirms that every scheduled job ran the day
before and raises an alarm for any that silently failed to appear. Two brokerage-auth
health checks verify that the read-only connection to the brokerage is alive and warn,
with two days of lead time, when the weekly re-authorization is about to be required
(see section 13). A database backup is taken and integrity-checked.

**Mid-morning — the qualifier decides eligibility.** Around the market open the system
takes a fresh snapshot of the research cohort and refreshes the earnings calendar, then
runs the **cycle qualifier**. The qualifier is the morning's centerpiece: for every name
in every cohort it produces one of four verdicts — a full-size *go*, a half-size
*downsize*, a *skip*, or a *pending* verdict awaiting more data — by running the gates
described in section 3. Immediately afterward an AI commentary pass writes a short
plain-language read of the pre-cycle setup. By mid-morning, the trader can see exactly
which names are eligible for the day and at what size.

**Late afternoon — marking the book and building the alert.** After the close the system
pulls the day's closing prices, marks every open spread to its current value, ingests any
filled orders from the brokerage, reconciles the qualifier's earlier verdicts against
what actually happened, scores the expected-value ranking used to break ties among
qualified candidates, and freezes the entry-context snapshot for any newly placed trade.
All of this feeds the **daily alert**, which is generated at 4:45 PM and is the single
most important thing the trader reads each day (section 9).

**Evening — refreshing the research substrate.** In the evening the system ingests the
day's full options-chain history from the data vendor, refreshes the macro-sensitivity
profile against the new data, and runs the overnight auto-promotion scan that watches for
names earning their way toward cohort membership. This is the layer that keeps the
research current; the trader rarely needs to look at it directly, but it is why the next
morning's qualifier is working from fresh evidence.

**Quarterly.** Four times a year the full cohort-selection pipeline re-runs against newly
available cycles, so that cohort membership and per-ticker recommendations stay current
with the most recent year of market history rather than ossifying at their original
values.

The practical takeaway for the trader is simple: **check the qualifier mid-morning to see
what is eligible, and read the daily alert after the close to decide what to do.**
Everything else runs underneath.

---

## 9. Reading the Daily Alert

The daily alert is an email the system sends every weekday afternoon. It is the
operating summary of the entire book and the primary surface the trader acts on. It is
organized so that the most time-sensitive information — what to consider closing — comes
first, and the context that informs those decisions follows.

**Regime header.** The alert opens with a plain-language statement of the current market
regime: where the broad market sits relative to its long-term trend, what the volatility
environment looks like, and which of the early-warning rings (section 3) are flashing. A
*trajectory watch* line flags conditions that are trending toward a threshold but have
not yet crossed it, so that a deteriorating backdrop is visible before it becomes a
problem.

**Close-side actions.** Next comes the list of open positions that warrant attention:
positions at or near their fifty-percent profit target, positions reaching the
twenty-one-day management cue, and positions whose underlying has moved toward or through
a short strike. Each carries a concrete reason — how much cushion remains to the short
strike, whether a strike has been breached, and the regime context — and, where relevant,
a suggested limit price and the worst-case fill range, so the trader can place a close
order by hand with the numbers already worked out.

**Construction blocks.** For names that cleared the qualifier as eligible, the alert
provides a construction block: the specific structure, the strikes implied by the
per-ticker moneyness rules, the per-leg bid/mid/ask, a limit credit or debit to work, and
inline warnings (for example, a name trading materially below its long-term trend, or a
spread whose credit-to-width ratio is too thin). Candidates are ordered by their
expected-value ranking so the most attractive sit at the top. These blocks are
recommendations to be entered manually, never orders the system places.

**Position health and discipline.** A position-health section reports each open
position's status as green, yellow, or red against its loss line, collapsing the healthy
positions to a count so that the ones needing attention stand out. A discipline prompt
surfaces any position the trader earlier flagged as one they might not hold under real
money — the paper-to-live psychological-gap check — so that those positions get a second
look.

**Macro-concentration warnings.** When more than one candidate clears on the same evening,
the alert checks whether they share a macro-sensitivity tier (section 6) and, if so,
appends a cross-sector concentration warning. This is informational — it does not block
anything — but it makes a hidden correlation visible at the moment of decision.

The alert is designed to be read top to bottom in a few minutes. The discipline it
encodes is that no position should reach a loss cap or a management deadline without the
trader having been told, in plain language and ahead of time, that it was coming.

---

## 10. The Dashboard

Alongside the daily email, the system runs a live dashboard the trader can open in a
browser at any time. Where the alert is a once-a-day snapshot, the dashboard is the
interactive view — useful for looking deeper into anything the alert surfaces, or for
checking the book mid-cycle. It is organized as a set of pages:

- **Open Positions** — every open position with its current mark, its distance from its
  loss-cap line, and whether it has crossed the twenty-one-day management threshold.
- **Daily Alert** — the same content as the email, rendered for the screen, so the latest
  alert can be re-read without digging through email.
- **Cohorts** — which names belong to each structure's cohort and which carry validated
  per-ticker recommendations for moneyness or wing width.
- **Analytics** — cross-trade learning queries over the trade ledger (section 12): how
  structures and names have performed, each reported with its sample size and an honest
  adequacy flag so that thin evidence is never mistaken for a conclusion.
- **Post-Mortem** — the interpretive synthesis produced after each cycle closes
  (section 11).
- **Bond Portfolio** — the held-to-maturity fixed-income side of the account, kept
  visible alongside the options book for a complete picture of capital.
- **Macro Brief** — the current macro and rate-path backdrop, including the Fed-path
  probabilities the system ingests.
- **Pre-Cycle** — the morning's AI read of the setup for the cycle ahead.
- **Auto-Promotion** — the pipeline that watches names earning their way toward cohort
  membership, so the promotion process is auditable.
- **Filled Book** — a read-only, spread-level view of real filled orders reconciled from
  the brokerage, with credit or debit, fees, and realized profit and loss. This is the
  honest record of what was actually traded, kept separate from the algorithm's
  recommendations.

The dashboard reads from the same single database the rest of the system writes to, so
what it shows is always consistent with the alert and the post-mortem.

---

## 11. The Life of a Cycle

It helps to follow a single position from birth to post-mortem, because the daily rhythm
above is really the support structure for this longer arc. The natural unit of the system
is the monthly options-expiration cycle.

**Entry.** Roughly forty-five days before a monthly expiration (seventy-five for the
ZEBRA structure), the qualifier begins flagging eligible names. On a morning when a name
clears its gates, that afternoon's alert carries a construction block with the structure,
strikes, and a limit price. The trader reviews it, and if they agree, enters the order by
hand at the brokerage. Earnings-bias positions are the exception to the timing: they
enter one or three days before a scheduled announcement.

**Holding and management.** Once a position is open, the system marks it every afternoon
and reports its health in the alert. Three things can call for an exit, and whichever
comes first is the one taken: the position reaches its fifty-percent profit target; it
reaches the twenty-one-day management cue, at which point the decay-versus-tail-risk
math has turned against continuing to hold; or it touches its loss-cap line at twice the
entry credit. The alert flags each of these as it approaches, with the numbers to act on.
Every credit spread is meant to carry a stop at twice its entry credit from the moment it
is opened, so the maximum loss is bounded before the position is ever at risk — but
because the system never places orders, the trader enters that protective stop manually,
just as they enter the opening order.

**Close.** When the trader closes a position — at a target, at the management deadline, at
a stop, or by judgment — they report the close to the system, which updates its records.
After go-live, the brokerage reconciler also picks the fill up automatically from the
account and keeps the filled book in sync, net of fees.

**Post-mortem.** After a cycle's expiration has passed, the system runs a post-mortem. It
reads every artifact it generated during that cycle through a sealed reasoning framework
and produces an interpretive synthesis aimed at one question: was the framework executed
consistently, and where did discipline drift? A central tool here is the
held-to-expiration counterfactual — what the cycle's profit *would* have been had every
spread simply been held to expiration. The gap between that number and what was actually
realized is the cycle's discipline scoreboard: a positive gap means profit was left on
the table by closing winners early; a negative gap means losing positions were correctly
cut before they reached their caps. No single cycle proves anything — the system is
explicit that one cycle describes rather than validates — but the trend in that gap across
many cycles is the most honest measure the system keeps of whether the doctrine is being
followed.

---

## 12. The Trade Ledger

Every layer described so far either decides what to trade or reports on what is open. The trade ledger is where the system *remembers* what it has done, in enough structured detail to learn from it. It is the connective tissue between three things the rest of the system otherwise keeps apart: what the framework recommended, the conditions it was recommended in, and what actually happened.

When a position closes, the ledger does not merely record its profit or loss. It enriches that outcome with the full context of the trade's life: the market regime at the moment of entry and again at exit, the qualifier verdict and the plain-language reason that admitted the position in the first place — its provenance — the worst drawdown the position endured while it was held, and an inferred classification of how it ended, whether at a profit target, at the twenty-one-day management cue, at the loss-cap stop, or by the trader's discretion. A position placed without a matching qualifier recommendation is flagged as off-script, so the disciplined trades and the discretionary ones never blur together in the record.

The value of assembling all of this in one place is that it turns a pile of closed tickets into queryable experience. The system can then ask the questions that improve the framework rather than the ones that manage a single position: which structures actually paid, and in which regimes; whether a given name has earned its place in the cohort or merely survived a friendly market; whether trades taken off-script fared better or worse than the ones the framework chose; whether the system's exits are systematically early or late. These are the questions the post-mortem (section 11) and the Analytics view of the dashboard (section 10) draw on, and the ledger is the substrate that makes them answerable.

One discipline governs every answer the ledger produces: each statistic is reported alongside an adequacy flag tied to its sample size, so that a result drawn from six trades is labeled preliminary and never carries the weight of one drawn from forty. This is the same refusal to over-read thin evidence that runs through the rest of the system. The ledger accumulates experience honestly — marking how much of it there actually is — rather than manufacturing confidence the sample cannot support. Over time it is the system's institutional memory: the record against which the framework's real behavior, not its backtested promise, is judged.

---

## 13. Routine Maintenance and What Can Break

The system is built to run unattended, but a small number of things need a human, and a
few failure modes are worth knowing about.

**The weekly brokerage re-authorization.** The brokerage's security model requires the
data connection to be re-authorized through an interactive login once every seven days.
This cannot be automated — it is a hard limit imposed by the brokerage. The system gives
two days of warning before the deadline through its morning health check, and the chore
itself is a single command run once a week. If the re-authorization lapses, the system
loses its read-only data feed until it is renewed; it cannot lose the ability to trade,
because it never had it.

**Failure alerts.** Every scheduled job runs through a wrapper that emails the trader if
the job fails, and a separate morning heartbeat raises an alarm for any job that should
have run but did not appear at all. Behind both sits an external dead-man's-switch
service: if the whole machine were to go dark — losing power, losing network — the
absence of the expected check-in trips an alert from the outside, so a total outage
cannot pass unnoticed. The design principle is that a silent failure is the only
unacceptable kind; a loud one the trader can respond to.

**Backups and restore.** The database is backed up every morning with an
integrity-checked, consistent snapshot, keeping a rolling week of copies and pruning
older ones only after a new good backup is confirmed. A tested restore procedure exists
and is documented, so a corrupted database is a recoverable event rather than a
catastrophe.

**Data staleness.** When a data feed goes stale, the system marks the affected figures as
stale in the alert rather than silently presenting old numbers as current. The operating
rule is that stale data may inform context but must never be the basis for a live trading
decision; the trader treats a stale flag as a reason to verify before acting.

For day-to-day operation, the maintenance burden comes down to one recurring task — the
weekly re-authorization — plus responding to the occasional failure email. Everything
else maintains itself.

---

## 14. Paper-Test Discipline and Going Live

The system is run under a deliberate paper-testing regime before any real capital is
committed. Through the paper-test window, every recommendation is tracked exactly as it
would be in live trading — same gates, same sizing, same management rules — but the trades
are recorded on paper rather than placed with money. The purpose is to accumulate enough
real, forward, out-of-sample cycles to judge whether the framework holds up outside the
backtest, and to surface the psychological gap between what is easy to hold on paper and
what one would actually hold under real money.

The paper-test window is binding. It is not shortened because a result looks compelling,
and it is governed by pre-registered falsification criteria: each structure has a written
standard it must meet — a minimum number of closed cycles, positive expectancy at the
friction actually incurred rather than the friction assumed in backtest — and failing that
standard forces a rewrite of the relevant part of the plan rather than an extension of the
testing. Quietly rolling the paper-test forward to avoid a verdict is itself recognized as
a failure mode and ruled out in advance.

When the window closes and the framework has earned its keep, "going live" is a single
human decision: the trader begins entering the system's recommendations with real money
instead of on paper. At that point the paper-trading records are purged so the book starts
clean, while every piece of collected market and signal history is preserved — the
research substrate is just as valid for live trading as it was for paper. The mechanics of
trading do not change at go-live, because the system never traded in the first place. What
changes is only the trader's decision to act on its advice with real capital.

---

## Appendix — Glossary

This document aims to be self-contained, but it uses the working vocabulary of options
trading and of the project itself. The terms below are grouped by kind. Where a term is
also defined in the body, the glossary entry is the short reference version.

**The project's name**

- **Max pain / MaxPain** — In options, the *max-pain* price is the strike level at which the largest dollar amount of open contracts would expire worthless — i.e., the price that inflicts the maximum aggregate loss on option *buyers*. The project began as an investigation of trading around that level and kept the name, but the max-pain thesis itself has since been tabled; the system today is a credit-spread and long-volatility playbook, and the name is historical rather than descriptive.

**The four trading structures**

- **Credit spread** — An options position opened for a net *credit* (you are paid premium): sell one option and simultaneously buy a further out-of-the-money option of the same type as protection. Maximum profit is the credit collected; maximum loss is the distance between the two strikes minus that credit. Defined-risk by construction.
- **Bull put** — A put credit spread: sell a put and buy a lower-strike put beneath it. Collects a credit and keeps it if the underlying stays above the short strike. A neutral-to-bullish position.
- **Bear call** — A call credit spread: sell a call and buy a higher-strike call above it. Keeps its credit if the underlying stays below the short strike. A neutral-to-bearish position; in this system it only deploys in confirmed weak, high-volatility regimes.
- **Inverted fly** — A defined-risk, three-strike butterfly-type structure configured "inverted" so that it profits from a *large* move in the underlying or an expansion in implied volatility — the long-volatility opposite of a premium-selling butterfly. Its key parameter is *wing width* (see below).
- **ZEBRA (Zero-Extrinsic Back-Ratio)** — A structure built to behave like 100 shares of stock with capped risk: it carries roughly +1.00 delta (moves nearly dollar-for-dollar with the underlying) but with almost no time-decay exposure and a maximum loss bounded at the debit paid to open it. In this system each ZEBRA is paired with a protective long put (the "overlay").

**Options mechanics**

- **Strike / short strike** — The price at which an option may be exercised. The *short* strike is the leg you sold; it is the side that is at risk if the underlying moves against you.
- **Spot** — The current trading price of the underlying stock or ETF.
- **Premium / credit / debit** — *Premium* is an option's price. Opening a position for a *credit* means you are paid net premium; for a *debit* means you pay net premium.
- **Moneyness** — The strike's position relative to spot: *in-the-money* (ITM), *at-the-money* (ATM), or *out-of-the-money* (OTM). See section 4.
- **Delta** — How much an option's price moves for a $1 move in the underlying, also read loosely as the option's probability of finishing in-the-money. A 0.30-delta short strike is moderately out-of-the-money (~30%); 0.50 is at-the-money; 0.70 is in-the-money.
- **Theta** — Time decay: how much value an option loses per day as expiration approaches. Working in the credit-spread seller's favor.
- **Gamma** — The rate at which delta itself changes as the underlying moves. Gamma spikes in the final weeks before expiration, which is why a short premium position grows riskier near expiry — the basis for the twenty-one-day management cue.
- **Implied volatility (IV)** — The amount of future price movement the market is pricing into an option. Higher IV means richer premiums.
- **IV rank (IVR)** — Where current IV sits within its own trailing range (here, the prior 252 trading days ≈ one year), scaled 0 to 1. An IVR above 0.5 means volatility is in the upper half of its yearly range — "elevated."
- **Term structure / inversion** — The curve of implied volatility across expiration dates. It is normally upward-sloping (longer-dated IV higher, called *contango*); it is *inverted* when near-term IV exceeds longer-dated IV — a stress or event signal that historically rewards long-volatility structures.
- **Wing / wing width** — In butterfly-type structures, the distance from the central body to the outer protective strikes. It sets the structure's width, cost, and payoff profile; in this system it is tuned per ticker for inverted flies.
- **Long volatility (long-vol)** — A position that gains when volatility or the size of price moves increases — the opposite stance to selling premium.
- **Mark / mark-to-market** — Valuing an open position at its current market price, as opposed to its entry price.
- **Bid / mid / ask** — The best price a buyer will pay (bid), the best a seller will accept (ask), and the midpoint between them (mid).
- **Buy-to-close / stop-limit / good-til-canceled (GTC)** — Order types. *Buy-to-close* exits a short option. A *stop-limit* triggers when the mark reaches a set level and then fills at a specified limit price. *Good-til-canceled* keeps an order working until it fills or is cancelled.
- **Roll / rolling** — Closing a position that is expiring or under threat and reopening a similar one at a later expiration or different strikes.
- **OpEx** — Options expiration — the monthly expiration date (standard third-Friday cycle) around which the system's trade cycles are organized. Used interchangeably with "options-expiration cycle."
- **DTE / T-minus notation** — Days to expiration. "T-21," "T-14," etc. denote a number of days before expiration (T-21 = twenty-one days remaining).
- **Defined-risk** — A position whose worst-case loss is fixed and known at entry. The system trades nothing else (see section 5).
- **Friction / slippage** — The real cost of trading beyond the quoted price: the bid/ask spread, fees, and *slippage* — the gap between the price you expect and the price you actually get filled at.
- **Tail risk** — The risk of rare but large adverse moves in the tails of the return distribution.

**Statistical and research-method terms**

- **Pre-registration** — Committing in writing to a hypothesis and its exact pass/fail criteria *before* running the test, so a result cannot be rationalized after the fact. The system's central research discipline.
- **Walk-forward validation** — Building or fitting a rule on an earlier *training* window of history, then confirming it on a later, untouched *validation* window. A rule must hold in both, with the same direction, to be trusted — the guard against curve-fitting.
- **Out-of-sample** — Data not used to build a rule, reserved to test it honestly. Forward paper-trading is out-of-sample by construction.
- **Wilcoxon signed-rank test** — A paired, non-parametric statistical test (it makes no assumption that the data are normally distributed) used to compare a candidate's per-cycle results against an alternative.
- **Expectancy** — The average profit or loss per trade expected over many repetitions.
- **Falsification criteria** — Pre-set conditions that, if met, declare a rule or structure to have failed, forcing a rewrite rather than a quiet extension of testing.
- **Regime** — The prevailing market environment — calm bull, confirmed bear, recovery, and so on — that conditions which rules apply and at what size. See the regime stages below.
- **Coefficient (sensitivity coefficient)** — In the macro-sensitivity profile (section 6), a number measuring how strongly a name has historically moved in response to a given economic factor.

**System-specific terms**

- **Cohort** — The validated list of tickers eligible to trade a given structure. Membership is earned through the selection pipeline (section 2).
- **Tier 1 / Tier 2** — Within a cohort, the original validated core (tier 1) versus the later auto-promotion expansion (tier 2). Both trade identically; see section 2.
- **Cycle qualifier (the qualifier)** — The weekday-morning job that issues a verdict for every cohort name.
- **Verdict (go / downsize / skip / pending)** — The qualifier's per-name decision: full size, half size, skip this cycle, or defer pending more data.
- **Construction block / construction card** — The daily alert's per-trade recipe: structure, strikes, per-leg prices, and a limit credit or debit to work. A recommendation to enter by hand, never an order the system places.
- **Rings / the cascade** — The three-ring regime-health monitor (AI-concentration, technology-index, broad-market) whose simultaneous red flags raise the informational *exit cascade*. Distinct from the descriptive *breadth ring* and *overnight-drift watch* (section 3).
- **H1** — The bear-confirmation signal underlying the regime stages: SPY trading below its 200-day moving average *and* SPY IV rank above 0.5. High-precision as confirmation, weak as early warning.
- **Held-to-expiration counterfactual** — The post-mortem's discipline scoreboard: what a cycle's profit *would* have been had every spread simply been held to expiration, compared against what was actually realized (sections 5 and 11).
- **Post-mortem** — The after-cycle review that reads the cycle's artifacts through a sealed reasoning framework to ask whether the framework was executed consistently.
- **The book** — The set of currently open positions.
- **Harness** — The software framework — scheduled jobs, alert, dashboard, database — that runs the whole system.
- **Trade ledger** — The system's structured memory of closed trades: each outcome enriched with entry/exit regime, qualifier provenance, drawdown, and exit type, so performance can be sliced by structure, name, and regime. The cross-trade learning substrate (section 12).
- **MAE (maximum adverse excursion)** — The worst unrealized loss a position reached at any point during the hold, regardless of where it finally closed. A measure of how much heat a trade took.
- **Exit type** — The inferred reason a trade closed: profit target, twenty-one-day management cue, loss-cap stop, or trader discretion.
- **Off-script** — A position that was placed without a matching qualifier recommendation — i.e., a discretionary trade taken outside the framework. Flagged in the ledger so it is never pooled with framework-driven trades.
- **Adequacy flag** — A sample-size honesty label attached to every aggregate statistic: preliminary (<10 trades), suggestive (<20), developing (<30), adequate (≥30). Keeps thin evidence from being read as a conclusion.

**The five regime stages**

The regime-transition framework classifies the market into five stages that govern new-entry mix and sizing (it never force-closes existing positions). The live system computes Stages 1–3 directly from the daily signal; Stages 4 and 5 are the conceptual deepening and recovery phases of a confirmed bear.

- **Stage 1 — Soft-downsize.** Early-warning triggers fire (elevated IV rank, price near a falling 200-day average, or an inverted term structure with a high VIX). New bull puts cut to half size; new ZEBRAs paused.
- **Stage 2 — Below trend, unconfirmed.** SPY breaks below its 200-day moving average but IV rank is still under 0.5. Continued caution; bear calls still not permitted.
- **Stage 3 — Confirmed bear (H1 active).** SPY below its 200-day average *and* IV rank above 0.5. Bear calls become permitted on their cohort; long-volatility structures continue.
- **Stage 4 — Decline toward the trough.** The drawdown plays out; rollable bull puts manage via their roll triggers, others absorb defined-risk losses, and ZEBRA exposure ages toward its capped maximum loss.
- **Stage 5 — Recovery (H1 false-positive zone).** New bear calls stop once SPY closes back above its 200-day average; bull puts and ZEBRAs stay in their downsized cadence until both the price and volatility conditions fully clear.

---

*Part I is roughly 2,900 words; Part II adds the operating guide. Together the document is
both an explanation of how the system works and a guide to running it. The canonical,
version-controlled mechanics remain in `TRADING_PLAN.rtf`.*
