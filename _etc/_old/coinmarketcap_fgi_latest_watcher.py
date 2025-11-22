#!/usr/bin/env python3
# -*- coding: utf-8 -*-



import os
import sys
import time
import json
import argparse
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from math import ceil

import logging
import requests
import pandas as pd


# ---------- logging ----------
"""
DEBUG - Ez egy debug üzenet
INFO - Ez egy információ
WARNING - Ez egy figyelmeztetés
ERROR - Ez egy hibaüzenet
CRITICAL - Ez már nagyon komoly hiba
"""

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s',datefmt='%Y-%m-%d %H:%M:%S')

file_handler = logging.FileHandler('coinmarketcap_fgi_watcher.log')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# ---------- .env ----------
THIS_FILE = Path(__file__).resolve()
ENV_PATH = THIS_FILE.parent / "myapi.env"

from dotenv import load_dotenv
load_dotenv(dotenv_path=ENV_PATH, override=True)


# ---------- Const ----------
FGI_HIST_URL = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/historical"
FGI_LATEST_URL = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest"

OUT_DAILY = Path("data/fgi/fear_greed_index.csv")
OUT_4H    = Path("data/fgi/fear_greed_index_4h.csv")
OBS_LOG   = Path("data/fgi/fgi_update_observations.csv")

FOUR_H_OPENS = (0, 4, 8, 12, 16, 20)
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}

# ---------- Utils ----------

def get_api_key():
    key = (
        os.getenv("CMC_API_KEY")
    )
    if not key:
        logger.critical("HIBA: hiányzik a CMC_API_KEY (myapi.env).")
        sys.exit(2)
    return key

def retry_request(session, method, url, **kwargs):
    attempt = 0
    base = 1.0
    while True:
        try:
            resp = session.request(method, url, timeout=30, **kwargs)
            if resp.status_code in RETRYABLE:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            elif resp.status_code >= 400 and resp.status_code not in RETRYABLE:
                logger.error(f"Unhandled HTTP ERROR, Status Code: {resp.status_code}, Response: {resp}")
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


def fetch_latest(session, api_key):
    """
    Return:
      {
        'value': float, 'classification': str,
        'update_dt': datetime(UTC or None),
        'raw_update_time': str or None
      }  or None
    """
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": api_key}
    resp = retry_request(session, "GET", FGI_LATEST_URL, headers=headers, params={})
    data = resp.json()
    d = data.get("data")
    logger.debug(f"fetch_latest:{d}")

    return d

def get_next_update_time(d):
    last_updt_time = datetime.fromisoformat(d.get("update_time"))
    next_updt_time = last_updt_time + timedelta(minutes=15)
    logger.debug(f"last update time: {last_updt_time}, next_updt_time: {next_updt_time}")
    remain_in_sec =  ceil((next_updt_time - datetime.now(timezone.utc)).total_seconds()) + 5
    logger.debug(f"time_to_next_updt in ms: {remain_in_sec}")

    return remain_in_sec



# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="CMC FGI downloader (watch-by-default, strict 4H rule).")
    ap.add_argument("--once", action="store_true", help="Egyszeri futás, nem figyel (watch kikapcsolása).")
    ap.add_argument("--interval", type=int, default=300, help="Watch módban két próbálkozás közötti idő (mp). Alap: 300.")
    ap.add_argument("--per-page", type=int, default=500, help="Historikus oldalméret (max 500).")
    ap.add_argument("--daily-path", type=Path, default=OUT_DAILY, help="Napi CSV kimenet.")
    ap.add_argument("--h4-path", type=Path, default=OUT_4H, help="4 órás CSV kimenet.")
    ap.add_argument("--log-updates", action="store_true", help="Csak akkor ír az observations logba, ha változott a latest.update_time.")
    args = ap.parse_args()
    # print(args)
    prev_latest = None
    while True:
        ses = requests.Session()
        latest = fetch_latest(ses, get_api_key())
        if latest != prev_latest:
            prev_latest = latest
            logger.info(f"New Latest arrived: value:{latest.get('value')}, update_time: {latest.get('update_time')}, value_classification: {latest.get('value_classification')}")
        sleeptime = get_next_update_time(latest)
        if sleeptime >= 0:
            logger.debug(f"sleep until {sleeptime} seconds")
            time.sleep(sleeptime)
        else:
            logger.debug(f"non positive sleeptime ({sleeptime}) wait 10 seconds and try again...")
            time.sleep(10)

    return 0

if __name__ == "__main__":
    sys.exit(main())
