# ORATS Ingest Pipeline

Downloads ORATS historical zip archive from FTP, unzips, and converts to Parquet partitioned by year/month.

## One-time setup

1. Edit `config.py` — fill in `ORATS_FTP_USER` and `ORATS_FTP_PASS` from the ORATS purchase email. Update `ORATS_FTP_HOST` and `ORATS_REMOTE_BASE` if ORATS's delivery uses a different host or root path than the defaults.
2. `pip install pandas pyarrow` if not present (already installed at `/opt/homebrew/bin/python3.11`).

## Discover the remote layout first

Before running downloads, confirm the FTP directory structure matches what the script expects (one folder per year containing `.zip` files with a date in the filename):

```
python3.11 ingest.py discover
```

This lists the top-level directory entries. Adjust `ORATS_REMOTE_BASE` in `config.py` if needed.

## Run the pipeline

**Tier 1 priority years (2020, 2022, 2024, 2018), full pipeline:**
```
python3.11 ingest.py all --tier 1
```

**Single year:**
```
python3.11 ingest.py all --year 2020
```

**One stage at a time (for debugging):**
```
python3.11 ingest.py download --year 2020
python3.11 ingest.py unzip --year 2020
python3.11 ingest.py parquet --year 2020
```

## Idempotency

Each stage tracks what it has already processed in `manifest.json`. Re-running is safe — it skips completed work. If a stage fails partway, the next run picks up where it left off.

## Output layout

```
data/orats/
├── raw/{year}/*.zip              # downloaded archives
├── csv/{year}/*.csv              # unzipped CSVs
├── parquet/year={YYYY}/month={MM}/{YYYY-MM-DD}.parquet
└── manifest.json                 # progress tracker
```

Query the Parquet with pandas or DuckDB. Example:

```python
import pandas as pd
df = pd.read_parquet("~/MaxPain_Project/data/orats/parquet", filters=[("year","==",2020), ("month","==",3)])
```

## Logs

Progress + errors at `~/MaxPain_Project/logs/orats_ingest.log`.
