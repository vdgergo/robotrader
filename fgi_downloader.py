#!/usr/bin/env python3
# -*- coding: utf-8 -*-


"""

CoinMarketCap Fear & Greed Index letöltő – Verzió 0.2
====================================================

Modul célja
-----------
A CoinMarketCap API biztosít Fear & Greed Index (FGI) adatokat két végponton keresztül:
- **historical**: napi bontásban, az adott nap kivételével,
- **latest**: az aktuális nap értéke, elvileg 15 perces frissítéssel (a gyakorlatban nem mindig megbízható).

A modul feladata ezen adatok letöltése, feldolgozása és CSV fájlokban való tárolása.  
Az FGI értékeket a stratégiában a kedvezőtlen piaci időszakok kiszűrésére használom.

Futtatás
---------
A script futtatható:
- cron job segítségével, **4 óránként** (UTC szerint: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00), vagy
- manuálisan, bármikor.

Működés
--------
1. Megnézi, hogy van-e korábbi CSV fájl. Ha igen, betölti a napi és 4 órás adatokat. Ha nem, üres DataFrame-eket hoz létre.
2. Lekéri a `latest` adatot.
3. A `latest` kulcsait a saját formátumra alakítja.
4. A lekért értéket besorolja a megfelelő gyertyába (`open_time`, `close_time`) és hozzáadja / felülírja az adatkeretben.
5. Ellenőrzi, hogy hiányos-e a napi vagy 4 órás sorozat.  
   - Ha igen, letölti a teljes `historical` adatot, és kiegészíti vele a hiányzó részeket.  
   - A 4 órás adatok a napi sorozatból generálódnak (a `latest` előtti adatok alapján).
6. Az adatokat biztonságosan fájlba írja (atomic write).

Fontos tulajdonság
-------------------
A program nem csak letölti az aktuális (latest) adatot, hanem ellenőrzi, hogy a korábbi adatok rendelkezésre állnak-e.
Ha nem vagy hiányosak, a historicalt is lekéri és a hiányzó adatokat abból pótolja. Ha az aktuális gyertyán belül kerül futatásra (aktuáls open-time és close-time között), akkor frissíti a már letötlött adatot is, de csak azt.

ENV
----
API kulcsot a `myapi.env` fájlban kell megadni a script mellett:
    CMC_API_KEY=IDE_A_KULCS

Távlati bővítési, fejlesztési lehetőségek:
    - Logging bővítése, hogy a hiba esetén annak jelentkezése és az állapot megismerhető látható legyen az ok feltárása érdekében.
    - File lock beépítése. Ez garantálja, hogy mindig csak egy példányban fusson.
    - parquet, vagy adatbázis alapú adatmentés
    - teszt írás

"""

import os
import sys
import time
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

file_handler = logging.FileHandler('logs/coinmarketcap_fgi.downloader.log')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# --- API kulcs betöltése .env fájlból ---
THIS_FILE = Path(__file__).resolve()
ENV_PATH = THIS_FILE.parent / "config/myapi.env"

from dotenv import load_dotenv
load_dotenv(dotenv_path=ENV_PATH, override=True)


# --- Állandók (konstansok) ---
FGI_HIST_URL = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/historical"
FGI_LATEST_URL = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest"

OUT_DAILY = Path("data/fgi/fear_greed_index.parquet")
OUT_4H    = Path("data/fgi/fear_greed_index_4h.parquet")

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

def atomic_write_parquet(df, path, **to_csv_kwargs):
    """Parquet biztonságos mentése átmeneti fájlon keresztül (atomic write)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', delete=False, dir=str(path.parent), suffix='.tmp') as tmp:
        df.to_parquet(tmp, index=True)
        tmp_name = tmp.name
    os.replace(tmp_name, path)

def load_old_data(path):
    """
    Betölti a meglévő FGI parquet-et. Ha nincs fájl, üres DataFrame-et hoz létre
    egységes oszlopokkal: open_time, close_time, fear_greed_value, fear_greed_classification.
    """
    try:
        old_df = pd.read_parquet(path)
        old_df.index = pd.to_datetime(old_df.index, utc=True)
        # oszlopok: close_time is legyen UTC-aware
        old_df["close_time"] = pd.to_datetime(old_df["close_time"], utc=True, errors="coerce")
        old_df.index.name = "open_time"
        logger.info(f"Existing data loaded from {path}, rows={len(old_df)}")
    except FileNotFoundError:
        empty_idx = pd.DatetimeIndex([],tz='UTC',name='open_time')
        old_df = pd.DataFrame(columns=["close_time","fear_greed_value","fear_greed_classification"],index=empty_idx)

        logger.info(f"No existing data at {path}; create empty  DataFrame")

    return old_df

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


def floor_to_nh(inp, n_hours):
    if not isinstance(inp,datetime):
        dt = pd.to_datetime(int(inp), unit="s", utc=True)
    else:
        dt = inp
    h = (dt.hour // n_hours) * n_hours
    return dt.replace(hour=h, minute=0, second=0, microsecond=0)

def convert_column_names(d):
    """
    d: eredeti dict
    mapping: {régi_kulcs: új_kulcs}
    """
    mapping = {"value": "fear_greed_value", "value_classification": "fear_greed_classification", "timestamp": "open_time"}
    out = {}
    for old_k, v in d.items():
        if old_k in mapping:
            out[mapping[old_k]] = v

    return out

def add_latest_to_old(latest_dict,old_df,h):
    latest_dict = latest_dict.copy()
    old_df = old_df.copy()
    start = floor_to_nh(latest_dict.get('open_time'),h)
    end = start + timedelta(hours=h) - timedelta(milliseconds=1)
    latest_dict['open_time'] = start
    latest_dict['close_time'] = end

    latest_idx = pd.to_datetime(latest_dict.get('open_time'),utc=True)
    del latest_dict['open_time']
    old_df.loc[latest_idx] = latest_dict
    old_df.sort_index(inplace=True)

    return old_df

def has_missing_periods(df):
    return True if len(df) < 3 else not bool(pd.infer_freq(df.index))   

def fill_missing_with_historic(historic,df_container,h):
    df_container = df_container.copy()
    modded_historic = []
    for d in historic:
        start = floor_to_nh(d.get('open_time'),h)
        end = start + timedelta(hours=h) - timedelta(milliseconds=1)
        d['open_time'] = start
        d['close_time'] = end
        modded_historic.append(d)

    new_df = pd.DataFrame(modded_historic)
    new_df = new_df.set_index('open_time')
    df_container = df_container.combine_first(new_df)
    return df_container

def daily_to_4h(df_daily):
    """
    A napi FGI értékek felosztása 4 órás gyertyákra (aznap minden gyertya ugyanazt kapja)
    """
    if df_daily is None or df_daily.empty:
        return pd.DataFrame(columns=["open_time", "close_time", "fear_greed_value", "fear_greed_classification"])
    rows = []
    for day, row in df_daily.iterrows():
        for h in FOUR_H_OPENS:
            start = datetime(day.year, day.month, day.day, h, 0, 0, tzinfo=timezone.utc)
            end = start + timedelta(hours=4) - timedelta(milliseconds=1)
            rows.append({
                "open_time": start,
                "close_time": end,
                "fear_greed_value": float(row["fear_greed_value"]),
                "fear_greed_classification": row["fear_greed_classification"],
            })
    df = pd.DataFrame(rows).set_index("open_time").sort_index()
    last_closed_4h = floor_to_nh(datetime.now(tz=timezone.utc),4) - timedelta(hours=4)
    df = df.loc[:last_closed_4h]
    return df



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
    Aktuális legfrissebb FGI lekérése.
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
            output = None

        if is_today_data(d.get('update_time')):
            isYesterdayData = False
            dtime = datetime.now(tz=timezone.utc)
            d['timestamp'] = trunc(dtime.timestamp())
            d.pop('update_time')
            logger.info(f"Today's FGI arrived: {d}")
            output = d
        else:
            logger.info(f"No today value yet; will retry in 30 minutes...")
            if (time.time() - start_time) / 60 > max_wait_minutes:
                logger.error(f"Timeout: did not receive today's FGI within {max_wait_minutes} minutes.")
                output = None
                break
                
            
            time.sleep(30)

    return output


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

    # 1. Megnézi, hogy van-e fájl, ha igen behívja a napi és a 4 órás adatokat fájlból. Ha nincs, létrehoz egy-egy üres df-et.
    daily_df_container = load_old_data(args.daily_path)
    h4_df_container = load_old_data(args.h4_path)

    # 2. Lekéri a latest napi adatot
    latest = fetch_latest(ses, get_api_key())
    # 3. átalakítja a latest kulcsait saját formátumra
    latest = convert_column_names(latest)
    # 4. A lekért fgi értéknél meghatározza, hogy melyik gyertyába esik, vagyis hogy adott felbontásnál mi az open_time és close_time értéke, majd hozzáadja a fájlból beolvasott df-hez, felülírva.
    daily_df_container = add_latest_to_old(latest,daily_df_container,24)
    h4_df_container = add_latest_to_old(latest,h4_df_container,4)

    # 5. Megnézi, hogy van hiányzó adat a napiból vagy a 4 órásból, ha igen lekéri az összes régi napi adatot, hozzáfűzi az újat. Utána 4 órásnál is megnézi, hogy hiányzik-e, ha igen a régi adatokból készít egy 4 órásat, úgy hogy csak a latest előtti adatokat tartalmazza, majd kipótolja vele a hiányzó részeket.
    if has_missing_periods(daily_df_container) or has_missing_periods(h4_df_container):
        historic_daily = fetch_historical(ses,get_api_key(),args.per_page)
        historic_daily = [convert_column_names(d) for d in historic_daily]
        daily_df_container = fill_missing_with_historic(historic_daily,daily_df_container,24)

        if has_missing_periods(h4_df_container):
            historic_4h = daily_to_4h(daily_df_container)
            h4_df_container = h4_df_container.combine_first(historic_4h)

    # 6. Kiírja az adatokat fájlba biztonságosan atomic write módszerrel
    atomic_write_parquet(daily_df_container,args.daily_path)
    atomic_write_parquet(h4_df_container,args.h4_path)

    return 0

if __name__ == "__main__":
    sys.exit(main())
