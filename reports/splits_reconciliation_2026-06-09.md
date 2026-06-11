# Split-ledger reconciliation (2026-06-09)

- Tickers scanned: 679 | adjustment-worthy splits: 166 | feed ok: 679 | feed unavail: 0
- Status tally: CONFIRMED=137, DETECTOR_SUSPECT=38, FEED_FRACTIONAL_REVIEW=16, FEED_ONLY=27, FEED_UNCONFIRMED=12, MANUAL=2, OUT_OF_RANGE=27


## Detector-missed splits, price-confirmed & promoted

| ticker | ex-date | split | live cohort |
|---|---|---|---|
| APH | 2014-10-10 | 2:1 |  |
| ASST | 2026-02-06 | 1:20 |  |
| BOIL | 2020-04-21 | 1:10 |  |
| FCEL | 2024-11-11 | 1:30 |  |
| GME | 2022-07-22 | 4:1 |  |
| HUT | 2023-12-04 | 1:5 |  |
| KORU | 2025-02-10 | 1:10 |  |
| MARA | 2017-10-30 | 1:4 |  |
| MARA | 2019-04-08 | 1:4 |  |
| MSTR | 2024-08-08 | 10:1 |  |
| NLY | 2022-09-26 | 1:4 |  |
| SCO | 2022-05-26 | 1:5 |  |
| SIL | 2015-11-18 | 1:3 |  |
| SIRI | 2024-09-10 | 1:10 |  |
| SOXS | 2020-08-28 | 1:12 |  |
| SOXS | 2024-04-15 | 1:10 |  |
| SQQQ | 2014-01-24 | 1:4 |  |
| SQQQ | 2022-01-13 | 1:5 |  |
| SQQQ | 2025-11-20 | 1:5 |  |
| TQQQ | 2014-01-24 | 2:1 |  |
| TQQQ | 2022-01-13 | 2:1 |  |
| TQQQ | 2025-11-20 | 2:1 |  |
| TSLA | 2020-08-31 | 5:1 | YES |
| UCO | 2022-05-26 | 4:1 |  |
| UNG | 2024-01-24 | 1:4 |  |
| VXX | 2021-04-23 | 1:4 |  |
| WEAT | 2025-11-25 | 1:5 |  |

## Feed 'splits' rejected (no ORATS discontinuity — dividend/spinoff/feed error)

| ticker | ex-date | split | live cohort |
|---|---|---|---|
| BKSY | 2024-09-09 | 1:8 |  |
| ET | 2014-01-27 | 2:1 |  |
| ET | 2015-07-27 | 2:1 |  |
| FCEL | 2019-05-09 | 1:12 |  |
| GOLD | 2022-06-07 | 2:1 | YES |
| GOOG | 2014-03-27 | 2:1 |  |
| GSK | 2022-07-22 | 1.25:1 |  |
| NVAX | 2019-05-10 | 1:20 |  |
| RTX | 2020-04-03 | 1.6:1 |  |
| SGI | 2020-11-24 | 4:1 |  |
| SLS | 2019-11-08 | 1:50 |  |
| UCO | 2020-04-21 | 1:25 |  |

## Fractional feed splits needing manual review (NOT applied)

| ticker | ex-date | split | live cohort |
|---|---|---|---|
| AA | 2016-11-01 | 1.25:1 |  |
| DHR | 2016-07-05 | 1.33:1 |  |
| EXC | 2022-02-02 | 1.4:1 |  |
| FDX | 2026-06-01 | 1.25:1 |  |
| FLEX | 2024-01-03 | 1.33:1 |  |
| FTI | 2021-02-16 | 1.33:1 |  |
| GE | 2024-04-02 | 1.25:1 | YES |
| GSK | 2022-07-19 | 1:1.25 |  |
| HPE | 2017-04-03 | 1.33:1 |  |
| ITUB | 2018-11-28 | 1.5:1 |  |
| SATS | 2019-09-11 | 1.25:1 |  |
| SKM | 2021-11-30 | 1:1.67 |  |
| T | 2022-04-11 | 1.33:1 |  |
| UNIT | 2025-08-04 | 1:1.67 |  |
| WDC | 2025-02-24 | 1.33:1 |  |
| XLF | 2016-09-19 | 1.25:1 |  |

## Detector-suspect splits excluded (feed contradicts — likely data artifacts)

| ticker | ex-date | split | live cohort |
|---|---|---|---|
| AI | 2020-12-15 | 1:36 |  |
| APPS | 2025-02-06 | 1:2 |  |
| BBT | 2025-09-03 | 2:1 |  |
| BTU | 2015-10-01 | 1:13 |  |
| BTU | 2017-04-12 | 1:12 |  |
| CLMT | 2016-04-18 | 2:1 |  |
| COHR | 2022-09-09 | 6:1 |  |
| CORZ | 2022-10-27 | 5:1 |  |
| CORZ | 2024-01-26 | 1:45 |  |
| CPRI | 2024-10-25 | 2:1 |  |
| DELL | 2019-03-01 | 1:4 |  |
| DJX | 2018-01-09 | 1:100 |  |
| ETE | 2014-01-27 | 2:1 |  |
| ETE | 2015-07-27 | 2:1 |  |
| FIG | 2025-08-04 | 1:12 |  |
| GOOG | 2014-04-03 | 2:1 |  |
| HPQ | 2015-11-02 | 2:1 |  |
| HTZ | 2016-07-01 | 1:4 |  |
| HTZ | 2021-11-10 | 1:14 |  |
| LBTYA | 2014-03-05 | 2:1 |  |
| META | 2022-06-10 | 1:14 | YES |
| NVAX | 2019-02-28 | 3:1 |  |
| P | 2026-04-20 | 1:8 |  |
| PATH | 2021-05-07 | 1:16 |  |
| PL | 2021-12-14 | 10:1 |  |
| QURE | 2025-11-03 | 2:1 |  |
| RAI | 2015-09-01 | 2:1 |  |
| RKT | 2014-08-28 | 2:1 |  |
| RKT | 2020-09-30 | 3:1 |  |
| S | 2021-07-09 | 1:6 |  |
| SE | 2018-01-05 | 3:1 |  |
| SNOW | 2020-09-22 | 1:10 |  |
| TAL | 2016-12-01 | 1:5 |  |
| TE | 2025-03-04 | 20:1 |  |
| VXX | 2016-08-09 | 1:4 |  |
| VXX | 2017-08-23 | 1:4 |  |
| WOLF | 2025-09-30 | 1:25 |  |
| Z | 2015-08-17 | 3:1 |  |

## Splits using feed ratio over a differing detector ratio (feed authoritative)

| ticker | ex-date | feed | detector factor |
|---|---|---|---|
| BOIL | 2023-06-23 | 1:20 | 22.0 |
| FCEL | 2015-12-04 | 1:12 | 10.0 |
| GRPN | 2020-06-11 | 1:20 | 15.0 |
| HYMC | 2023-11-15 | 1:10 | 12.0 |
| OUST | 2023-04-21 | 1:10 | 9.0 |
| SHOP | 2022-06-29 | 10:1 | 0.09091 |
| SNXX | 2026-06-03 | 8:1 | 0.14286 |
| SOXL | 2021-03-02 | 15:1 | 0.0625 |
| SOXS | 2019-06-28 | 1:10 | 9.0 |
| SOXS | 2022-03-28 | 1:10 | 9.0 |
| SPCE | 2024-06-17 | 1:20 | 17.0 |
| UVIX | 2025-01-15 | 1:10 | 9.0 |

Ledger: `config/splits_ledger.csv` (consumed by `lib.adjusted_close`). Rejected/review items are deliberately excluded from price adjustment.
