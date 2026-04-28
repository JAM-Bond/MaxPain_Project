#!/usr/bin/env python3.11
"""ORATS historical data ingest pipeline.

Stages: discover → download → unzip → parquet. Each stage idempotent; safe to rerun.

Usage:
    python3.11 ingest.py discover                    # list remote structure
    python3.11 ingest.py download --year 2020
    python3.11 ingest.py unzip --year 2020
    python3.11 ingest.py parquet --year 2020
    python3.11 ingest.py all --tier 1                # full pipeline for Tier 1 years
    python3.11 ingest.py all --year 2020
    python3.11 ingest.py all --tier 2 --cleanup      # delete zips/CSVs after downstream success
    python3.11 ingest.py cleanup                     # retroactive: prune files whose stage is complete
"""
import argparse
import json
import logging
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import paramiko

sys.path.insert(0, str(Path(__file__).parent))
import config as C


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(C.LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("orats")


DATE_RX = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")

NUMERIC_COLS = [
    "stkPx", "yte", "strike",
    "cVolu", "cOi", "pVolu", "pOi",
    "cBidPx", "cValue", "cAskPx", "pBidPx", "pValue", "pAskPx",
    "cBidIv", "cMidIv", "cAskIv", "smoothSmvVol",
    "pBidIv", "pMidIv", "pAskIv",
    "iRate", "divRate", "residualRateData",
    "delta", "gamma", "theta", "vega", "rho", "phi", "driftlessTheta",
    "extVol", "extCTheo", "extPTheo", "spot_px",
]


def load_manifest() -> dict:
    if C.MANIFEST_PATH.exists():
        return json.loads(C.MANIFEST_PATH.read_text())
    return {"downloaded": [], "unzipped": [], "parquet": []}


def save_manifest(m: dict) -> None:
    C.MANIFEST_PATH.write_text(json.dumps(m, indent=2, sort_keys=True))


def sftp_connect():
    if not C.ORATS_FTP_USER or not C.ORATS_FTP_PASS:
        raise SystemExit("Fill ORATS_FTP_USER and ORATS_FTP_PASS in config.py")
    transport = paramiko.Transport((C.ORATS_FTP_HOST, C.ORATS_FTP_PORT))
    transport.connect(username=C.ORATS_FTP_USER, password=C.ORATS_FTP_PASS)
    sftp = paramiko.SFTPClient.from_transport(transport)
    log.info("SFTP connected to %s:%d", C.ORATS_FTP_HOST, C.ORATS_FTP_PORT)
    return sftp, transport


def parse_date(name: str):
    m = DATE_RX.search(name)
    if not m:
        return None
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()


def stage_discover() -> None:
    """Walk the remote tree and print directory/file layout."""
    sftp, transport = sftp_connect()
    try:
        log.info("Remote base: %s", C.ORATS_REMOTE_BASE)
        entries = sftp.listdir(C.ORATS_REMOTE_BASE)
        log.info("Year-level entries (%d):", len(entries))
        for e in sorted(entries):
            log.info("  %s", e)
        if entries:
            sample = sorted(entries)[0]
            sub = f"{C.ORATS_REMOTE_BASE}/{sample}"
            try:
                sub_entries = sftp.listdir(sub)
                log.info("Sample of %s (%d files, first 5):", sub, len(sub_entries))
                for e in sorted(sub_entries)[:5]:
                    log.info("  %s", e)
            except IOError as e:
                log.warning("Could not list %s: %s", sub, e)
    finally:
        sftp.close()
        transport.close()


def stage_download(year: int) -> None:
    manifest = load_manifest()
    downloaded = set(manifest["downloaded"])
    parqueted = set(manifest["parquet"])

    remote_year_path = f"{C.ORATS_REMOTE_BASE.rstrip('/')}/{year}"
    local_dir = C.RAW_DIR / str(year)
    local_dir.mkdir(parents=True, exist_ok=True)

    sftp, transport = sftp_connect()
    try:
        remote_files = sftp.listdir(remote_year_path)
    except IOError as e:
        log.error("Cannot list %s: %s — check ORATS_REMOTE_BASE or year folder in config.py", remote_year_path, e)
        sftp.close()
        transport.close()
        return

    files = sorted([f for f in remote_files if f.lower().endswith(".zip")])
    log.info("Year %d: %d zip files remote", year, len(files))

    skipped = 0
    try:
        for i, fname in enumerate(files, 1):
            key = f"{year}/{fname}"
            csv_key = key.replace(".zip", ".csv")
            local_path = local_dir / fname
            if csv_key in parqueted:
                skipped += 1
                continue
            if key in downloaded and local_path.exists():
                continue
            try:
                sftp.get(f"{remote_year_path}/{fname}", str(local_path))
                downloaded.add(key)
                if i % 20 == 0 or i == len(files):
                    log.info("  [%d/%d] %s", i, len(files), fname)
                    manifest["downloaded"] = sorted(downloaded)
                    save_manifest(manifest)
            except Exception as e:
                log.error("Download failed for %s: %s", fname, e)
                if local_path.exists():
                    local_path.unlink()
        if skipped:
            log.info("Year %d: skipped %d files already parqueted", year, skipped)
    finally:
        sftp.close()
        transport.close()

    manifest["downloaded"] = sorted(downloaded)
    save_manifest(manifest)
    log.info("Year %d download complete: %d files", year, len(files))


def stage_unzip(year: int, cleanup: bool = False) -> None:
    src_dir = C.RAW_DIR / str(year)
    dst_dir = C.CSV_DIR / str(year)
    dst_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    unzipped = set(manifest["unzipped"])
    parqueted = set(manifest["parquet"])

    zips = sorted(src_dir.glob("*.zip"))
    log.info("Year %d: unzipping %d files%s", year, len(zips), " (cleanup=on)" if cleanup else "")

    for i, zpath in enumerate(zips, 1):
        key = f"{year}/{zpath.name}"
        csv_key = key.replace(".zip", ".csv")
        if csv_key in parqueted:
            if cleanup and zpath.exists():
                zpath.unlink()
            continue
        if key in unzipped:
            if cleanup and zpath.exists():
                zpath.unlink()
            continue
        try:
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(dst_dir)
            unzipped.add(key)
            if cleanup:
                zpath.unlink()
            if i % 20 == 0 or i == len(zips):
                log.info("  [%d/%d] %s", i, len(zips), zpath.name)
        except zipfile.BadZipFile as e:
            log.error("Bad zip %s: %s", zpath.name, e)

    manifest["unzipped"] = sorted(unzipped)
    save_manifest(manifest)
    log.info("Year %d unzip complete", year)


def reconcile_manifest_with_disk() -> None:
    """Scan existing parquet files and add any missing keys to the parquet manifest.
    Makes the manifest self-healing after crashes or interrupted runs."""
    if not C.PARQUET_DIR.exists():
        return
    manifest = load_manifest()
    ingested = set(manifest["parquet"])
    added = 0
    for pq_path in C.PARQUET_DIR.rglob("*.parquet"):
        date_str = pq_path.stem  # YYYY-MM-DD
        if len(date_str) != 10 or date_str[4] != "-":
            continue
        year = date_str[:4]
        csv_key = f"{year}/ORATS_SMV_Strikes_{date_str.replace('-','')}.csv"
        if csv_key not in ingested:
            ingested.add(csv_key)
            added += 1
    if added:
        log.info("Reconciled %d existing parquet files into manifest", added)
        manifest["parquet"] = sorted(ingested)
        save_manifest(manifest)


def stage_parquet(year: int, cleanup: bool = False) -> None:
    src_dir = C.CSV_DIR / str(year)
    manifest = load_manifest()
    ingested = set(manifest["parquet"])

    csvs = sorted(src_dir.rglob("*.csv"))
    log.info("Year %d: ingesting %d CSVs to Parquet%s", year, len(csvs), " (cleanup=on)" if cleanup else "")

    rows_total = 0
    for i, csv_path in enumerate(csvs, 1):
        key = f"{year}/{csv_path.name}"
        if key in ingested:
            if cleanup and csv_path.exists():
                csv_path.unlink()
            continue
        trade_date = parse_date(csv_path.name)
        if trade_date is None:
            log.warning("Cannot parse date from %s — skipping", csv_path.name)
            continue
        try:
            df = pd.read_csv(csv_path, low_memory=False)
        except Exception as e:
            log.error("Read failed for %s: %s", csv_path.name, e)
            continue

        for col in NUMERIC_COLS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["trade_date"] = pd.to_datetime(trade_date)
        df["year"] = trade_date.year
        df["month"] = trade_date.month

        out_dir = C.PARQUET_DIR / f"year={trade_date.year}" / f"month={trade_date.month:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{trade_date.isoformat()}.parquet"
        df.to_parquet(out_path, engine="pyarrow", compression="snappy", index=False)
        rows_total += len(df)
        ingested.add(key)
        if cleanup:
            csv_path.unlink()
        if i % 20 == 0 or i == len(csvs):
            log.info("  [%d/%d] %s — %d rows", i, len(csvs), csv_path.name, len(df))
            manifest["parquet"] = sorted(ingested)
            save_manifest(manifest)

    manifest["parquet"] = sorted(ingested)
    save_manifest(manifest)
    log.info("Year %d parquet complete: %d rows written", year, rows_total)


def stage_cleanup() -> None:
    """Retroactively prune files whose downstream stage has completed.

    Safe: only removes a zip if its key is in `unzipped`, and only removes a CSV
    if its key is in `parquet`. Never deletes anything without manifest evidence
    that downstream processing succeeded.
    """
    manifest = load_manifest()
    unzipped = set(manifest["unzipped"])
    ingested = set(manifest["parquet"])

    zips_removed = 0
    for zpath in C.RAW_DIR.rglob("*.zip"):
        key = f"{zpath.parent.name}/{zpath.name}"
        if key in unzipped:
            zpath.unlink()
            zips_removed += 1

    csvs_removed = 0
    for csv_path in C.CSV_DIR.rglob("*.csv"):
        year_dir = csv_path.relative_to(C.CSV_DIR).parts[0]
        key = f"{year_dir}/{csv_path.name}"
        if key in ingested:
            csv_path.unlink()
            csvs_removed += 1

    log.info("Cleanup complete: removed %d zips, %d CSVs", zips_removed, csvs_removed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["discover", "download", "unzip", "parquet", "all", "cleanup"])
    parser.add_argument("--year", type=int)
    parser.add_argument("--tier", type=int, choices=[1, 2, 3])
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete zip after successful unzip; delete CSV after successful parquet")
    args = parser.parse_args()

    if args.stage == "discover":
        stage_discover()
        return

    if args.stage == "cleanup":
        stage_cleanup()
        return

    reconcile_manifest_with_disk()

    if args.year:
        years = [args.year]
    elif args.tier == 1:
        years = C.TIER_1
    elif args.tier == 2:
        years = C.TIER_2
    elif args.tier == 3:
        years = C.TIER_3
    else:
        parser.error("pass --year or --tier")

    for year in years:
        if args.stage in ("download", "all"):
            stage_download(year)
        if args.stage in ("unzip", "all"):
            stage_unzip(year, cleanup=args.cleanup)
        if args.stage in ("parquet", "all"):
            stage_parquet(year, cleanup=args.cleanup)


if __name__ == "__main__":
    main()
