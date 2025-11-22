#!/usr/bin/env python3
# -*- coding: utf-8 -*-


"""
Vezió 0.1

CoinMarketCap Fear & Greed Index (FGI) downloader – napi + 4 órás kimenettel

A program célja a CoinMarketCap API-ból származó Fear & Greed Index (FGI) adatok letöltése és CSV-be mentése.

Működés röviden:
- Nem fut folyamatosan: futtatható éjfél után cron jobbal, az OHLCV adatlekérés indítása után, vagy bármikor kézzel.
- Kétféle API-hívásból dolgozik: `historical` (több nap/év visszamenőleg) és `latest` (aktuális nap).
- Lépések az I. verzióban:
  1. Letölti a historikus napi adatokat.
  2. A `latest` hívással figyeli, mikor jelenik meg az aznapi friss érték. Ha még tegnapi, akkor vár és újra próbálkozik, amíg meg nem jön a mai adat.
  3. Az új adatot a historikussal egységes formátumba alakítja.
  4. Összefűzi a régivel, eltéréseket felülír, majd napi CSV-be menti.
  5. A napi adatokat 4 órás bontású CSV-vé alakítja.
  6. Kilép.

Futtatható cron jobként (éjfél után) vagy kézzel bármikor.

ENV:
  myapi.env (a fájl mellé)
    CMC_API_KEY=IDE_A_KULCS
"""

import os
import sys
import time
import json
import argparse
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from math import trunc

import logging
import requests
import pandas as pd


# --- Logging beállítás ---
"""
Logging szintek:
    DEBUG - Ez egy debug üzenet
    INFO - Ez egy információ
    WARNING - Ez egy figyelmeztetés
    ERROR - Ez egy hibaüzenet
    CRITICAL - Ez már nagyon komoly hiba
"""

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

file_handler = logging.FileHandler('coinmarketcap_fgi.downloader.log')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# --- API kulcs betöltése .env fájlból ---
THIS_FILE = Path(__file__).resolve()
ENV_PATH = THIS_FILE.parent / "myapi.env"

from dotenv import load_dotenv
load_dotenv(dotenv_path=ENV_PATH, override=True)


# --- Állandók (konstansok) ---
FGI_HIST_URL = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/historical"
FGI_LATEST_URL = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest"

OUT_DAILY = Path("data/fgi/fear_greed_index.csv")
OUT_4H    = Path("data/fgi/fear_greed_index_4h.csv")

FOUR_H_OPENS = (0, 4, 8, 12, 16, 20)
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}

PARQUET = False  # Parquet mentés opció (jövőbeni használatra)

# --- Segédfüggvények ---

def get_api_key():
    """API kulcs beolvasása környezeti változóból"""
    key = os.getenv("CMC_API_KEY")
    if not key:
        logger.critical("HIBA: hiányzik a CMC_API_KEY (myapi.env).")
        sys.exit(2)
    return key

def atomic_write_csv(df, path, **to_csv_kwargs):
    """CSV biztonságos mentése átmeneti fájlon keresztül (atomic write)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), suffix='.tmp', newline='') as tmp:
        df.to_csv(tmp, lineterminator='\n', **to_csv_kwargs)
        tmp_name = tmp.name
    os.replace(tmp_name, path)

def check_old_data(path,new_df):
    """
    Meglévő CSV fájl összehasonlítása új adatokkal.
    - Ha nem létezik a fájl → csak az új adatok maradnak
    - Ha létezik → összevonás + duplikált sorok eldobása
    """

    try:
        old_df = pd.read_csv(path, index_col=0, parse_dates=True, encoding='utf-8')
        logger.info(f"Existing data loaded from {path}, rows={len(old_df)}")
    except FileNotFoundError:
        logger.info(f"No latest value; using only fetcehd data")
        return new_df.copy()
    

    logger.debug(f"Old daily data index min: {old_df.index.min()}, max: {old_df.index.max()}")
    logger.debug(f"New daily data index min: {new_df.index.min()}, max: {new_df.index.max()}")
    combined = pd.concat([old_df, new_df])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    return combined


def retry_request(session, method, url, **kwargs):
    """API hívás újrapróbálása exponenciális visszalépéssel és hibakezeléssel"""
    attempt = 0
    base = 1.0
    while True:
        try:
            resp = session.request(method, url, timeout=30, **kwargs)
            if resp.status_code in RETRYABLE:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            elif resp.status_code >= 400 and resp.status_code not in RETRYABLE:
                logger.error(f"Unhandled HTTP ERROR, Status Code: {resp.status_code}, Response: {resp}")
            logger.debug(f"HTTP {resp.status_code} OK: {url}")
            return resp
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            attempt += 1
            if attempt > 6:
                logger.error(f"Max retry exceeded: {e}")
                raise
            sleep_s = base * (2 ** (attempt - 1))
            if isinstance(e, requests.HTTPError) and getattr(e, "response", None) is not None:
                if e.response.status_code == 429:
                    sleep_s *= 1.5
            logger.error(f"Transient error ({attempt}/6): {e} -> sleep {sleep_s:.1f}s")
            time.sleep(sleep_s)

def is_today_data(isotime):
    """Ellenőrzi, hogy az adott ISO időpont aznapi-e UTC szerint"""
    now_utc = datetime.now(timezone.utc).date()
    ts = datetime.fromisoformat(isotime)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.date() == now_utc

# ---------- API ----------
def fetch_historical(session, api_key, per_page=500):
    """Historikus FGI adatok lekérése több oldalon keresztül"""
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": api_key}
    start = 1
    pages = 0
    all_items = []
    while True:
        pages += 1
        resp = retry_request(session, "GET", FGI_HIST_URL, headers=headers, params={"start": str(start), "limit": str(per_page)})
        data = resp.json()
        items = data.get("data")
        if not items:
            logger.warning(f"No items returned on page {pages}. Status: {resp.status_code}. Response: {data}")
            break
        all_items.extend(items)
        logger.info(f"Fetched historical page {pages}: rows={len(items)}, start={start}")
        if len(items) < per_page:
            break
        start += per_page
        time.sleep(0.2)
    return all_items

def fetch_latest(session, api_key ,max_wait_minutes=120):
    """
    Aznapi legfrissebb FGI lekérése.
    Várakozik, amíg az "update_time" a mai napra esik (max. 2 óra).
    """
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": api_key}
    start_time = time.time()
    isYesterdayData = True
    
    while isYesterdayData:
        resp = retry_request(session, "GET", FGI_LATEST_URL, headers=headers, params={})
        data = resp.json()
        d = data.get("data")
        if not d:
            logger.error(f"No items returned on latest. Return: {d}")
            return None

        if is_today_data(d.get('update_time')):
            isYesterdayData = False
            dtime = datetime.fromisoformat(d.get('update_time'))
            d['timestamp'] = trunc(dtime.timestamp())
            d.pop('update_time')
            logger.info(f"Today's FGI arrived: {d}")
            return d
        else:
            logger.info(f"No today value yet; will retry in 30 minutes...")
            if (time.time() - start_time) / 60 > max_wait_minutes:
                logger.error(f"Timeout: did not receive today's FGI within {max_wait_minutes} minutes.")
                return None
            
            time.sleep(30)

def daily_from_raw(items):
    """Historikus vagy latest JSON lista → napi bontású DataFrame"""
    cols = ["datetime", "value", "classification"]
    if not items:
        return pd.DataFrame(columns=cols).set_index("datetime")
    rows = []
    for it in items:
        ts = it.get("timestamp")
        dt = pd.to_datetime(int(ts), unit="s", utc=True).tz_convert(None).normalize()

        rows.append({
            "datetime": dt,
            "value": float(it.get("value")),
            "classification": it.get("value_classification")
        })
    df = pd.DataFrame(rows).set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df

def daily_to_4h(df_daily):
    """
    A napi FGI értékek felosztása 4 órás gyertyákra (aznap minden gyertya ugyanazt kapja)
    """
    cols = ["open_time", "close_time", "fear_greed_value", "fear_greed_classification"]
    if df_daily is None or df_daily.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for day, row in df_daily.iterrows():
        for h in FOUR_H_OPENS:
            start = datetime(day.year, day.month, day.day, h, 0, 0)
            end = start + timedelta(hours=4) - timedelta(milliseconds=1)
            rows.append({
                "open_time": start,
                "close_time": end,
                "fear_greed_value": float(row["value"]),
                "fear_greed_classification": row["classification"],
            })
    df = pd.DataFrame(rows).set_index("open_time").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


# --- CLI belépési pont ---
def main():
    ap = argparse.ArgumentParser(description="CMC FGI downloader (watch-by-default, strict 4H rule).")
    ap.add_argument("--per-page", type=int, default=500, help="Historikus oldalméret (max 500).")
    ap.add_argument("--daily-path", type=Path, default=OUT_DAILY, help="Napi CSV kimenet.")
    ap.add_argument("--h4-path", type=Path, default=OUT_4H, help="4 órás CSV kimenet.")
    ap.add_argument("--parquet", action="store_true", default=PARQUET, help="Mentés Parquet formátumban is.")
    args = ap.parse_args()

    logger.info("Starting FGI download process...")
    ses = requests.Session()
    latest = fetch_latest(ses, get_api_key())
    historical = fetch_historical(ses, get_api_key())
    historical.append(latest)
    all_data = historical

    daily_df = check_old_data(args.daily_path,daily_from_raw(all_data))
    hourly_4h_df = daily_to_4h(daily_df)

    atomic_write_csv(daily_df,args.daily_path)
    atomic_write_csv(hourly_4h_df,args.h4_path)

    return 0

if __name__ == "__main__":
    sys.exit(main())
