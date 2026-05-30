#!/usr/bin/env python3.11
"""Build the authoritative split ledger by reconciling three sources.

Sources, in order of authority:
  1. MANUAL  — `config/splits_manual.csv` (user-curated; always wins)
  2. FEED    — yfinance corporate actions (`lib.corporate_actions`; definitive
               ex-dates + ratios, catches fractional / earnings-adjacent splits
               the heuristic is blind to)
  3. DETECTOR— price-discontinuity heuristic (`lib.adjusted_close.detect_splits`;
               works offline, but misses non-integer / neighbor-guarded splits)

Output: `config/splits_ledger.csv` (TRACKED source of truth), columns
  ticker,date,factor,label,source,status
where `factor` is the multiplier applied to PRE-split prices to make a series
continuous (0.5 for 2:1 fwd, 10 for 1:10 rev) — exactly what
`lib.adjusted_close.back_adjust` consumes. `lib.adjusted_close` reads THIS ledger
(falling back to live detection only for tickers absent from it).

Reconciliation status per split:
  CONFIRMED      detector & feed agree (same ex-date ±tol, same factor)
  FEED_ONLY      feed has it, detector missed   ← the dangerous gap; would have
                 silently corrupted a 200-DMA/RS. Promoted into the ledger.
  DETECTOR_ONLY  heuristic found it, feed lacks it (feed gap, or a mis-detected
                 crash — flagged for review; kept, since the detector requires a
                 clean integer snap + calm neighbors)
  MISMATCH       both present but factors disagree  ← flagged loudly
  MANUAL         a manual override is present (wins regardless)
  FEED_UNAVAIL   network/parse failure; detector value used, flagged

Usage:
  python3.11 -m scripts.maintenance.build_splits_ledger            # all by_ticker
  python3.11 -m scripts.maintenance.build_splits_ledger --cohort   # 37 live names
  python3.11 -m scripts.maintenance.build_splits_ledger AAA BBB    # explicit list
  python3.11 -m scripts.maintenance.build_splits_ledger --no-network  # detector+manual only
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.adjusted_close import _load_raw, detect_splits  # noqa: E402
from lib.corporate_actions import fetch_splits           # noqa: E402

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"
LEDGER_PATH = ROOT / "config/splits_ledger.csv"
LEDGER_META = ROOT / "config/splits_ledger.meta.json"
MANUAL_PATH = ROOT / "config/splits_manual.csv"
LEGACY_MANUAL = ROOT / "data/profile/splits_manual.csv"   # pre-migration location
COHORT_PATH = ROOT / "data/profile/research_cohort_v15.parquet"

DATE_TOL_DAYS = 6      # ORATS discontinuity date vs official ex-date
FACTOR_TOL = 0.05      # relative tolerance when comparing factors


def load_manual() -> dict[str, list[dict]]:
    """Manual overrides keyed by ticker. Reads config/ then legacy data/ path.
    The CSV 'ratio' column is the price-discontinuity ratio (= factor): 0.1428
    for a 7:1 forward, 10 for a 1:10 reverse."""
    out: dict[str, list[dict]] = {}
    for path in (MANUAL_PATH, LEGACY_MANUAL):
        if not path.exists():
            continue
        with open(path) as f:
            for r in csv.DictReader(f):
                t = r.get("ticker", "").strip().upper()
                if not t:
                    continue
                factor = float(r["ratio"])
                out.setdefault(t, []).append({
                    "date": pd.Timestamp(r["date"]), "factor": factor,
                    "label": _factor_label(factor), "source": "manual",
                })
        if path == MANUAL_PATH and out:
            break   # prefer config/ if it has entries
    return out


def _factor_label(factor: float) -> str:
    inv = 1.0 / factor if factor else 0
    if factor < 1:   # forward split (price dropped); k:1 where k = 1/factor
        return f"{int(round(inv))}:1"
    return f"1:{int(round(factor))}"


# Adjustment-worthy statuses go into the ledger; everything else is flagged for
# review and explicitly NOT applied to prices.
#   DETECTOR_NOFEED   detector split on a ticker the feed couldn't cover → best
#                     effort, applied (the feed can't refute it).
#   DETECTOR_SUSPECT  detector split the feed actively contradicts (feed covers
#                     the ticker, lists OTHER splits, but not this one) → almost
#                     always a data artifact (e.g. META's corrupt 2022 block
#                     misread as a 14:1). Excluded; flagged.
LEDGER_STATUSES = {"MANUAL", "CONFIRMED", "FEED_ONLY", "DETECTOR_NOFEED"}


def _match(a: dict, b: dict) -> bool:
    return (abs((a["date"] - b["date"]).days) <= DATE_TOL_DAYS
            and abs(a["factor"] - b["factor"]) <= FACTOR_TOL * max(a["factor"], b["factor"]))


def _price_confirms(series: pd.Series, ex_date: pd.Timestamp, feed_ratio: float,
                    tol: float = 0.20):
    """Does the ORATS close show a split-sized discontinuity ≈ feed_ratio across
    ex_date? Returns True (confirmed), False (no such jump — reject), or None
    (split predates/ postdates our data → irrelevant)."""
    import math
    before = series[series.index < ex_date]
    after = series[series.index >= ex_date]
    if len(before) == 0 or len(after) == 0:
        return None
    actual = float(before.iloc[-1]) / float(after.iloc[0])   # old/new == new/old shares
    if actual <= 0:
        return False
    return abs(math.log(actual / feed_ratio)) <= math.log(1 + tol)


def reconcile_ticker(ticker: str, *, use_network: bool,
                     manual: list[dict]) -> list[dict]:
    """Return reconciled rows for one ticker (each tagged with a status)."""
    try:
        raw = _load_raw(ticker)
        det = detect_splits(raw)
    except Exception:
        raw, det = pd.Series(dtype=float), []
    feed = fetch_splits(ticker) if use_network else None
    feed_unavail = use_network and feed is None
    feed = feed or []

    rows: list[dict] = []
    used_det, used_feed = set(), set()

    # 1) manual overrides win outright; consume any detector/feed match.
    for m in manual:
        for i, d in enumerate(det):
            if i not in used_det and _match(m, d):
                used_det.add(i)
        for j, f in enumerate(feed):
            if j not in used_feed and abs((m["date"] - f["date"]).days) <= DATE_TOL_DAYS:
                used_feed.add(j)
        rows.append({**m, "status": "MANUAL"})

    # 2) feed-vs-detector reconciliation, with ORATS price-confirmation for any
    #    feed split the detector did not independently find.
    for j, f in enumerate(feed):
        if j in used_feed:
            continue
        match = next((i for i, d in enumerate(det)
                      if i not in used_det and abs((d["date"] - f["date"]).days) <= DATE_TOL_DAYS), None)
        if match is not None:
            d = det[match]
            used_det.add(match)
            agree = abs(d["factor"] - f["factor"]) <= FACTOR_TOL * max(d["factor"], f["factor"])
            # Date matches ⇒ a split definitely happened. The feed ratio is the
            # OFFICIAL one; the detector's is inferred from price and skews on
            # volatile/leveraged names (e.g. SHOP 10:1 misread as 11:1). Always
            # take the feed factor; note the detector delta for transparency.
            rows.append({"date": f["date"], "factor": f["factor"], "label": f["label"],
                         "source": "feed", "status": "CONFIRMED",
                         "detector_factor": round(d["factor"], 5) if not agree else None})
            continue
        # detector missed it → demand a real price discontinuity before trusting.
        conf = _price_confirms(raw, f["date"], f["ratio"])
        if conf is None:
            status = "OUT_OF_RANGE"            # no pre/post data; irrelevant no-op
        elif not conf:
            status = "FEED_UNCONFIRMED"        # feed says split, price disagrees → reject
        elif f.get("integer", True):
            status = "FEED_ONLY"               # price-confirmed integer split → promote
        else:
            status = "FEED_FRACTIONAL_REVIEW"  # ~5:4-vs-spinoff ambiguity → manual review
        rows.append({"date": f["date"], "factor": f["factor"], "label": f["label"],
                     "source": "feed", "status": status})

    # 3) detector-only leftovers. Trust them only when the feed couldn't refute
    #    them (feed unavailable). When the feed IS available and silent on this
    #    split, it's almost certainly a data artifact, not a real split.
    for i, d in enumerate(det):
        if i in used_det:
            continue
        rows.append({"date": d["date"], "factor": d["factor"], "label": d["label"],
                     "source": "detector",
                     "status": "DETECTOR_NOFEED" if feed_unavail else "DETECTOR_SUSPECT"})

    rows.sort(key=lambda r: r["date"])
    for r in rows:
        r["ticker"] = ticker
    return rows


def resolve_tickers(args) -> list[str]:
    if args.tickers:
        return [t.upper() for t in args.tickers]
    if args.cohort:
        return sorted(pd.read_parquet(COHORT_PATH)["ticker"].astype(str).str.upper().unique())
    return sorted(p.stem.upper() for p in BY_TICKER.glob("*.parquet"))


def live_set() -> set[str]:
    try:
        return set(pd.read_parquet(COHORT_PATH)["ticker"].astype(str).str.upper())
    except Exception:
        return set()


def _prior_keys(ledger_path: Path) -> set[tuple[str, str]]:
    """(ticker, date) of adjustment-worthy splits in the existing ledger."""
    if not ledger_path.exists():
        return set()
    try:
        df = pd.read_csv(ledger_path)
        return {(str(r.ticker).upper(), str(r.date)) for r in df.itertuples()}
    except Exception:
        return set()


def _prior_flag_keys(meta_path: Path) -> set[tuple[str, str, str]]:
    """(ticker, date, status) of flagged (non-ledger) items recorded last run."""
    if not meta_path.exists():
        return set()
    try:
        import json
        flags = json.loads(meta_path.read_text()).get("flagged", [])
        return {(f["ticker"].upper(), f["date"], f["status"]) for f in flags}
    except Exception:
        return set()


def _send_change_alert(new_splits, new_flags, live):
    """Email the human when the ledger gains a split or a live-name flag."""
    from lib.email_alert import send_html_alert
    lines = []
    if new_splits:
        lines.append("NEW splits added to the adjustment ledger:")
        for t, d, lbl in new_splits:
            tag = "  ◀ LIVE COHORT" if t in live else ""
            lines.append(f"  {t:6} {d}  {lbl}{tag}")
    if new_flags:
        lines.append("\nNEW flagged items needing review (NOT auto-applied):")
        for t, d, st, lbl in new_flags:
            tag = "  ◀ LIVE COHORT" if t in live else ""
            lines.append(f"  {t:6} {d}  {st}  {lbl}{tag}")
    lines.append("\nReview: config/splits_ledger.csv + latest reports/splits_reconciliation_*.md")
    body = "\n".join(lines)
    n_live = sum(1 for x in new_splits if x[0] in live) + sum(1 for x in new_flags if x[0] in live)
    subj = f"MaxPain Splits — {len(new_splits)} new, {len(new_flags)} flagged" + (f" ({n_live} LIVE)" if n_live else "")
    send_html_alert(subject=subj, text_body=body,
                    html_body=f"<pre style='font-family:Menlo,monospace;font-size:13px'>{body}</pre>")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--cohort", action="store_true", help="only the 37 live cohort names")
    ap.add_argument("--no-network", action="store_true", help="detector+manual only (skip feed)")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between feed calls")
    ap.add_argument("--alert", action="store_true",
                    help="email when new splits or new live-name flags appear vs the prior ledger")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    use_network = not args.no_network
    manual = load_manual()
    tickers = resolve_tickers(args)
    live = live_set()

    # Snapshot prior state (for --alert diff) BEFORE we overwrite anything.
    prior_splits = _prior_keys(LEDGER_PATH)
    prior_flags = _prior_flag_keys(LEDGER_META)

    all_rows: list[dict] = []
    n_feed_ok = n_feed_unavail = 0
    for k, t in enumerate(tickers, 1):
        rows = reconcile_ticker(t, use_network=use_network, manual=manual.get(t, []))
        all_rows.extend(rows)
        if any(r["status"] == "FEED_UNAVAIL" for r in rows):
            n_feed_unavail += 1
        elif use_network:
            n_feed_ok += 1
        if args.sleep and use_network:
            time.sleep(args.sleep)
        if not args.quiet and k % 50 == 0:
            print(f"  ...{k}/{len(tickers)}")

    df = pd.DataFrame(all_rows)
    ledger_rows = df[df["status"].isin(LEDGER_STATUSES)].copy()
    flagged = df[~df["status"].isin(LEDGER_STATUSES | {"OUT_OF_RANGE"})].copy()

    # Write the ledger (adjustment-worthy rows only).
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    cols = ["ticker", "date", "factor", "label", "source", "status"]
    out = ledger_rows[cols].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out["factor"] = out["factor"].round(6)
    out = out.sort_values(["ticker", "date"]).reset_index(drop=True)
    out.to_csv(LEDGER_PATH, index=False)

    tally = df["status"].value_counts().to_dict()

    # Manifest: every ticker that was reconciled here. `lib.adjusted_close` trusts
    # the ledger as authoritative for these (even those with zero split rows — that
    # means "reconciled, no real split", which SUPPRESSES detector artifacts like
    # META's phantom 14:1). Tickers absent from this set fall back to live detection.
    import json
    meta = {"built": date.today().isoformat(),
            "n_tickers": int(df["ticker"].nunique()),
            "scope": "cohort" if args.cohort else ("explicit" if args.tickers else "all_by_ticker"),
            "tally": {k: int(v) for k, v in tally.items()},
            "reconciled_tickers": sorted({t.upper() for t in tickers}),
            "flagged": [{"ticker": r["ticker"], "date": pd.Timestamp(r["date"]).strftime("%Y-%m-%d"),
                         "status": r["status"], "label": r.get("label", "")}
                        for _, r in flagged.iterrows()]}
    (LEDGER_PATH.parent / "splits_ledger.meta.json").write_text(json.dumps(meta, indent=1))
    print("=" * 72)
    print(f"  Split ledger: {len(out)} adjustment-worthy splits across {ledger_rows['ticker'].nunique()} tickers")
    print(f"  Tickers scanned: {len(tickers)}   feed ok: {n_feed_ok}   feed unavail: {n_feed_unavail}")
    print(f"  Status: " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    print(f"  Wrote {LEDGER_PATH.relative_to(ROOT)}")

    def _rows(status):
        return [r for _, r in flagged[flagged["status"] == status].iterrows()]

    feed_only = [r for _, r in ledger_rows[ledger_rows["status"] == "FEED_ONLY"].iterrows()]
    rejected = _rows("FEED_UNCONFIRMED")
    review = _rows("FEED_FRACTIONAL_REVIEW")
    suspect = _rows("DETECTOR_SUSPECT")
    corrected = ([r for _, r in ledger_rows[ledger_rows.get("detector_factor").notna()].iterrows()]
                 if "detector_factor" in ledger_rows else [])
    live_touch = [r for r in (feed_only + rejected + review + suspect) if r["ticker"] in live]

    if feed_only:
        print(f"\n  ✓ {len(feed_only)} DETECTOR-MISSED splits, price-confirmed & promoted to ledger:")
        for r in feed_only:
            tag = "  ◀ LIVE COHORT" if r["ticker"] in live else ""
            print(f"      {r['ticker']:6} {pd.Timestamp(r['date']).date()}  {r['label']}{tag}")
    if rejected:
        print(f"\n  ✗ {len(rejected)} feed 'splits' REJECTED (no ORATS price discontinuity — dividend/spinoff/feed error):")
        for r in rejected:
            tag = "  ◀ LIVE COHORT" if r["ticker"] in live else ""
            print(f"      {r['ticker']:6} {pd.Timestamp(r['date']).date()}  feed {r['label']}{tag}")
    if review:
        print(f"\n  ⚠ {len(review)} fractional feed splits need MANUAL review (5:4-vs-spinoff ambiguity; NOT applied):")
        for r in review:
            tag = "  ◀ LIVE COHORT" if r["ticker"] in live else ""
            print(f"      {r['ticker']:6} {pd.Timestamp(r['date']).date()}  feed {r['label']}{tag}")
    if suspect:
        print(f"\n  ✗ {len(suspect)} DETECTOR-SUSPECT splits EXCLUDED (feed covers the ticker but doesn't list them — likely data artifacts):")
        for r in suspect:
            tag = "  ◀ LIVE COHORT" if r["ticker"] in live else ""
            print(f"      {r['ticker']:6} {pd.Timestamp(r['date']).date()}  {r['label']}{tag}")
    if corrected:
        print(f"\n  ℹ {len(corrected)} splits used the FEED ratio over a differing detector ratio (feed authoritative):")
        for r in corrected:
            print(f"      {r['ticker']:6} {pd.Timestamp(r['date']).date()}  feed {r['label']} (detector factor was {r.get('detector_factor')})")
    if live_touch:
        print(f"\n  ►► {len(live_touch)} flagged item(s) touch LIVE cohort names — verify before the window.")
    elif use_network:
        print("\n  ✓ No unresolved discrepancies on live cohort names.")

    # Markdown report.
    report = ROOT / f"reports/splits_reconciliation_{date.today().isoformat()}.md"
    report.parent.mkdir(exist_ok=True)
    L = [f"# Split-ledger reconciliation ({date.today().isoformat()})\n",
         f"- Tickers scanned: {len(tickers)} | adjustment-worthy splits: {len(out)} | "
         f"feed ok: {n_feed_ok} | feed unavail: {n_feed_unavail}",
         f"- Status tally: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())) + "\n"]

    def _section(title, rows, extra=None):
        if not rows:
            return
        L.append(f"\n## {title}\n")
        L.append("| ticker | ex-date | split | live cohort |\n|---|---|---|---|")
        for r in rows:
            L.append(f"| {r['ticker']} | {pd.Timestamp(r['date']).date()} | {r['label']} | "
                     f"{'YES' if r['ticker'] in live else ''} |")

    _section("Detector-missed splits, price-confirmed & promoted", feed_only)
    _section("Feed 'splits' rejected (no ORATS discontinuity — dividend/spinoff/feed error)", rejected)
    _section("Fractional feed splits needing manual review (NOT applied)", review)
    _section("Detector-suspect splits excluded (feed contradicts — likely data artifacts)", suspect)
    if corrected:
        L.append("\n## Splits using feed ratio over a differing detector ratio (feed authoritative)\n")
        L.append("| ticker | ex-date | feed | detector factor |\n|---|---|---|---|")
        for r in corrected:
            L.append(f"| {r['ticker']} | {pd.Timestamp(r['date']).date()} | {r['label']} | {r.get('detector_factor')} |")
    L.append(f"\nLedger: `config/splits_ledger.csv` (consumed by `lib.adjusted_close`). "
             f"Rejected/review items are deliberately excluded from price adjustment.\n")
    report.write_text("\n".join(L))
    print(f"  Wrote {report.relative_to(ROOT)}")

    # --alert: email only when something actually changed vs the prior ledger.
    if args.alert:
        new_splits = [(str(r.ticker).upper(), str(r.date), r.label)
                      for r in out.itertuples()
                      if (str(r.ticker).upper(), str(r.date)) not in prior_splits]
        new_flags = [(r["ticker"].upper(), pd.Timestamp(r["date"]).strftime("%Y-%m-%d"),
                      r["status"], r.get("label", ""))
                     for _, r in flagged.iterrows()
                     if (r["ticker"].upper(), pd.Timestamp(r["date"]).strftime("%Y-%m-%d"),
                         r["status"]) not in prior_flags]
        new_live_flags = [f for f in new_flags if f[0] in live]
        if new_splits or new_live_flags:
            try:
                _send_change_alert(new_splits, new_live_flags, live)
                print(f"  ✉ Alert sent: {len(new_splits)} new split(s), {len(new_live_flags)} new live flag(s)")
            except Exception as e:
                print(f"  alert send failed (non-fatal): {e}")
        else:
            print("  ✓ No ledger changes vs prior — no alert.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
