"""GICS sector lookup for cohort symbols.

The sector-concentration cap (max 2 single names per GICS sector per OpEx)
requires a per-symbol sector tag. Source of truth is this module's hardcoded
override dict, which covers every name in the current cohorts plus a
sentinel `_ETF` for index/sector ETFs that are exempt from the cap.

ETFs are exempt because:
  - Broad-market indices (SPY/QQQ/IWM/DIA/SPX) ARE diversification by
    construction
  - Sector ETFs (XLU/XLK/HYG/GLD/EFA/SMH/TLT/KRE) are themselves single-
    sector exposures; if multiple sector-ETF entries ever stack we want
    that flagged separately, not via the same-name cap

`_UNKNOWN` is returned for any symbol not in the override dict. These are
treated as cap-exempt (no sector to group on) but flagged in logs so we
can backfill the override list when new cohort names enter.
"""
from __future__ import annotations

ETF_SENTINEL = "_ETF"
UNKNOWN_SENTINEL = "_UNKNOWN"

# Symbol → GICS sector (post-2018 reclassification).
# Coverage check: every name in scripts/qualifier/gate_config.py's COHORT_*
# lists as of 2026-05-15 is mapped here. New cohort additions need to be
# added here too — the qualifier logs a warning when it sees UNKNOWN.
SECTOR_OVERRIDES: dict[str, str] = {
    # ── Index / broad-market ETFs (exempt) ────────────────────────────
    "SPY":   ETF_SENTINEL,
    "SPX":   ETF_SENTINEL,
    "QQQ":   ETF_SENTINEL,
    "DIA":   ETF_SENTINEL,
    "IWM":   ETF_SENTINEL,
    # ── Sector / commodity / bond ETFs (exempt) ───────────────────────
    "XLU":   ETF_SENTINEL,
    "XLK":   ETF_SENTINEL,
    "XLF":   ETF_SENTINEL,
    "XLE":   ETF_SENTINEL,
    "XLP":   ETF_SENTINEL,
    "XLV":   ETF_SENTINEL,
    "GLD":   ETF_SENTINEL,
    "SLV":   ETF_SENTINEL,
    "EFA":   ETF_SENTINEL,
    "EEM":   ETF_SENTINEL,
    "SMH":   ETF_SENTINEL,
    "HYG":   ETF_SENTINEL,
    "TLT":   ETF_SENTINEL,
    "TMF":   ETF_SENTINEL,
    "VXX":   ETF_SENTINEL,
    "ARKK":  ETF_SENTINEL,
    "KRE":   ETF_SENTINEL,
    "BITO":  ETF_SENTINEL,
    # ── Communication Services ────────────────────────────────────────
    "GOOG":  "communication_services",
    "GOOGL": "communication_services",
    "META":  "communication_services",
    # ── Consumer Discretionary ────────────────────────────────────────
    "TJX":   "consumer_discretionary",
    "AMZN":  "consumer_discretionary",
    "TSLA":  "consumer_discretionary",
    "RCL":   "consumer_discretionary",
    "CMG":   "consumer_discretionary",
    "EXPE":  "consumer_discretionary",
    "MCD":   "consumer_discretionary",
    "BABA":  "consumer_discretionary",
    "CAR":   "consumer_discretionary",  # Avis - rental, classified as cons disc
    "LULU":  "consumer_discretionary",
    "NKE":   "consumer_discretionary",
    "HD":    "consumer_discretionary",
    # ── Consumer Staples ──────────────────────────────────────────────
    "WMT":   "consumer_staples",
    "KO":    "consumer_staples",
    "PG":    "consumer_staples",
    "EL":    "consumer_staples",
    "TGT":   "consumer_staples",
    "PEP":   "consumer_staples",
    # ── Energy ────────────────────────────────────────────────────────
    "COP":   "energy",
    "DVN":   "energy",
    "XOM":   "energy",
    "CNQ":   "energy",
    "RRC":   "energy",
    # ── Financials ────────────────────────────────────────────────────
    "WFC":   "financials",
    "JPM":   "financials",
    "GS":    "financials",
    "COF":   "financials",
    "MS":    "financials",
    "SCHW":  "financials",
    "USB":   "financials",
    # ── Health Care ───────────────────────────────────────────────────
    "HUM":   "health_care",
    "MRK":   "health_care",
    "JNJ":   "health_care",
    "ISRG":  "health_care",
    "UNH":   "health_care",
    "CNC":   "health_care",
    "CVS":   "health_care",
    # ── Industrials ───────────────────────────────────────────────────
    "DAL":   "industrials",
    "GE":    "industrials",
    "BA":    "industrials",
    "MMM":   "industrials",
    "GNRC":  "industrials",
    # ── Information Technology ────────────────────────────────────────
    "MSFT":  "information_technology",
    "AVGO":  "information_technology",
    "AMAT":  "information_technology",
    "NET":   "information_technology",
    "CIEN":  "information_technology",
    "GLW":   "information_technology",
    "ADBE":  "information_technology",
    "IBM":   "information_technology",
    "NVDA":  "information_technology",
    "AMD":   "information_technology",
    "INTC":  "information_technology",
    "LRCX":  "information_technology",
    "STX":   "information_technology",
    "CSCO":  "information_technology",
    "FSLR":  "information_technology",
    "TTD":   "information_technology",
    "PLTR":  "information_technology",
    "INTU":  "information_technology",
    "CRM":   "information_technology",
    "ACN":   "information_technology",
    "SAP":   "information_technology",
    "WDAY":  "information_technology",
    "TEAM":  "information_technology",
    "ZS":    "information_technology",
    "ADBE_alt": "information_technology",   # placeholder for typo safety
    "MSTR":  "information_technology",
    "PYPL":  "financials",  # actually Financials (post-2023 GICS reclassification)
    "SHOP":  "information_technology",
    "SPOT":  "communication_services",
    "TME":   "communication_services",
    "DKNG":  "consumer_discretionary",
    "SNAP":  "communication_services",
    "BMBL":  "communication_services",
    "HIMS":  "health_care",
    "UPST":  "financials",
    "RBLX":  "communication_services",
    "CPNG":  "consumer_discretionary",
    "CVNA":  "consumer_discretionary",
    "NOW":   "information_technology",
    # ── Materials ─────────────────────────────────────────────────────
    "NUE":   "materials",
    "SCCO":  "materials",
    "GOLD":  "materials",
    "CLF":   "materials",
    "RIO":   "materials",
    "NEM":   "materials",
    "KGC":   "materials",
    "MOS":   "materials",
    # ── Real Estate ───────────────────────────────────────────────────
    "IYR":   ETF_SENTINEL,
    # ── Utilities (individual names — XLU is ETF) ─────────────────────
    # Currently no single utility names in the cohort
    # ── Communications (legacy telecoms) ──────────────────────────────
    "VZ":    "communication_services",
    "TMUS":  "communication_services",
    # ── Consumer Discretionary (other) ────────────────────────────────
    "GM":    "consumer_discretionary",
    "F":     "consumer_discretionary",
    "LEN":   "consumer_discretionary",
    "KBH":   "consumer_discretionary",
    "PDD":   "consumer_discretionary",
    "XPEV":  "consumer_discretionary",
    "NCLH":  "consumer_discretionary",
    "MLCO":  "consumer_discretionary",
    "TIGR":  "financials",
    "BKLN":  ETF_SENTINEL,
    "JNK":   ETF_SENTINEL,
    # ── Consumer Staples (other) ──────────────────────────────────────
    "CAG":   "consumer_staples",
    "GIS":   "consumer_staples",
    "CELH":  "consumer_staples",
    "AR":    "energy",  # Antero Resources
    "B":     "industrials",  # Barnes Group
    "PFE":   "health_care",
    "FCX":   "materials",
    "EFX":   "industrials",
    "AXP":   "financials",
    # ── Health Care (other) ───────────────────────────────────────────
    "DHR":   "health_care",
    # ── Real estate operations (other) ────────────────────────────────
}


def get_sector(symbol: str) -> str:
    """Return GICS sector for a symbol, or sentinel.

    Returns:
        - A GICS sector slug (e.g. "financials", "information_technology")
          for individual stocks
        - "_ETF" for index / sector / commodity / bond ETFs (cap-exempt)
        - "_UNKNOWN" for symbols not in the override list
    """
    if symbol is None:
        return UNKNOWN_SENTINEL
    return SECTOR_OVERRIDES.get(symbol.upper(), UNKNOWN_SENTINEL)


def is_cap_exempt(symbol: str) -> bool:
    """ETFs and unmapped names are exempt from the sector-concentration cap.

    ETFs by design (indices/sector ETFs are not single-name concentration).
    Unknown by safety — we'd rather miss a cap violation than wrongly drop
    a valid candidate due to a missing entry in the override dict.
    """
    s = get_sector(symbol)
    return s in (ETF_SENTINEL, UNKNOWN_SENTINEL)
