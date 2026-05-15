# MaxPain Project — A High-Level Overview

*A personal options-trading research and execution system*

---

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

The universe begins at one hundred fifty actively-traded equities and exchange-traded funds with sufficient options-chain depth to admit credit spreads with realistic execution. From this universe, separate cohorts are constructed for each trading structure: a bull-put cohort of approximately thirty names, a bear-call cohort of fourteen names, an inverted-fly cohort of approximately thirty names, and a ZEBRA (zero-extrinsic back-ratio) cohort of twenty-one names split across two tier classifications. Each cohort is assembled through a structured selection pipeline.

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

Time-based exit is enforced by the twenty-one-day management cue. At twenty-one days remaining to expiration, the position is closed or rolled regardless of how much credit has been retained. The cue is rooted in the structural mathematics of options decay: at approximately twenty-one days remaining, the ratio between gamma exposure and theta accumulation flips against the credit-spread seller. Beyond that point, additional time spent in the position trades small additional decay benefit for materially expanded tail risk. Whatever profit has been captured by twenty-one days is the profit the trade earned.

Profit-target capture at fifty percent of credit collected remains as a secondary win-side reference, particularly for positions that decay quickly. Whichever exit fires first — fifty-percent capture, the twenty-one-day time cue, or the loss-cap stop — is the exit the trade takes.

All positions are defined-risk by construction. The system does not deploy uncovered short calls, uncovered short puts, or any structure whose maximum loss is not bounded at trade entry by the difference between the strike legs.

After each cycle closes, the system runs a held-to-expiration counterfactual against every closed position: what would the cycle's realized profit have been if every credit spread had been held to expiration? The delta between actual and held-to-expiration profit is the cycle's discipline scoreboard. A cycle in which the trader systematically exited too early shows a positive delta — profit left on the table. A cycle in which the trader correctly exited losing positions before they reached the loss cap shows a negative delta. Across many cycles, the trend in the delta becomes the most honest measure of whether the framework is being executed consistently.

---

*Roughly 2,100 words across approximately three pages at standard formatting.*
