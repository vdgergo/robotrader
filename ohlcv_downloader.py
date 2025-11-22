#!/usr/bin/env python3
"""
Robust Binance OHLCV downloader (cron-safe) + manifest írás

Főbb jellemzők
- myapi.env (BINANCE_API_KEY, BINANCE_API_SECRET) betöltése a script mappájából
- Single-instance lock (nem fut két példány egyszerre)
- Inkrementális FRISSÍTÉS, ha létezik CSV (előrefelé)
  * Külön kérésre: az utolsó gyertyát eldobja és ugyanattól az open_time-tól újrahúzza
- TELJES történet letöltés, ha nincs CSV (visszafelé paginálás a legrégebbi gyertyáig)
- Exponenciális backoff átmeneti hibákra
- Atomic CSV write (Windowson is üres sorok nélkül)
- Manifest JSON frissítés MINDEN párral (atomikusan)
- Argparse: --interval / --quote / --pairs / --outdir / --lockfile / --manifest / --grace-seconds
"""

# --- .env betöltése a projekt gyökeréből (myapi.env) ---
from pathlib import Path
from dotenv import load_dotenv
import os, sys, time, argparse, tempfile, traceback, json,  re
from typing import List, Optional, Dict

THIS_FILE = Path(__file__).resolve()
ENV_PATH = THIS_FILE.parent / "config/myapi.env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

import pandas as pd
import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException
from datetime import datetime, timezone, timedelta

# ---------- Segédfüggvények ----------

def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


class LockFile:
    """Egyszerű, többplatformos lock (O_CREAT|O_EXCL)."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.fd = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            self.fd = os.open(str(self.path), flags)
            os.write(self.fd, str(os.getpid()).encode())
            os.fsync(self.fd)
            log(f"Lock acquired: {self.path}")
        except FileExistsError:
            log(f"Another instance is running (lock exists: {self.path}). Exiting.")
            sys.exit(0)

    def release(self):
        try:
            if self.fd is not None:
                os.close(self.fd)
            if self.path.exists():
                self.path.unlink()
                log(f"Lock released: {self.path}")
        except Exception as e:
            log(f"Failed to release lock {self.path}: {e}")


def retry(callable_fn, max_retries=6, base_delay=1.0, **kwargs):
    """Retry helper átmeneti hálózati/API hibákra exponenciális backoff-fal."""
    attempt = 0
    while True:
        try:
            return callable_fn(**kwargs)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                BinanceRequestException,
                BinanceAPIException) as e:
            attempt += 1
            if attempt > max_retries:
                log(f"Max retries exceeded: {e}\n{traceback.format_exc()}")
                raise
            sleep_s = base_delay * (2 ** (attempt - 1))
            log(f"Transient error (attempt {attempt}/{max_retries}): {e}. Sleeping {sleep_s:.1f}s...")
            time.sleep(sleep_s)
        except Exception as e:
            log(f"Unexpected error: {e}\n{traceback.format_exc()}")
            raise


def make_client() -> Client:
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        log("ERROR: Please set BINANCE_API_KEY and BINANCE_API_SECRET in myapi.env")
        log(f"[DEBUG] Tried env path: {ENV_PATH} (exists: {ENV_PATH.exists()})")
        sys.exit(2)
    return Client(api_key, api_secret)


def list_pairs(client: Client, quote: str) -> List[str]:
    """Visszaadja az adott quote assethez (pl. 'USDT') tartozó spot szimbólumokat."""
    info = retry(client.get_exchange_info)
    symbols = info.get('symbols', [])
    pairs = [s['symbol'] for s in symbols
             if s.get('quoteAsset') == quote
             and s.get('status') == 'TRADING'
             and s.get('isSpotTradingAllowed', True)]
    pairs.sort()
    return pairs


def to_dataframe(klines: list) -> pd.DataFrame:
    cols = ['open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore']
    df = pd.DataFrame(klines, columns=cols)
    df = df[['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time']]
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df['close_time'] = pd.to_datetime(df['close_time'], unit='ms', utc=True)
    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    df.set_index('open_time', inplace=True)
    return df


def atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Biztonságos CSV mentés (Windows + Linux kompatibilis, üres sorok nélkül)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    # index_name = df.index.name or "index"
    # df[index_name] = df.index  # indexből oszlop
    # df.reset_index(drop=True, inplace=True)  # megszünteti index szerepét

    with tempfile.NamedTemporaryFile('wb', delete=False, dir=str(path.parent), suffix='.tmp') as tmp:
        tmp_name = tmp.name
        df.to_parquet(tmp, engine='pyarrow',index=True)
    os.replace(tmp_name, path)


def load_existing_parquet(path: Path) -> Optional[pd.DataFrame]:
    if path.exists():
        try:
            df = pd.read_parquet(path)
            return df if not df.empty else None
        except Exception as e:
            log(f"Warning: failed to read existing Parquet {path}: {e}. Will rebuild file.")
    return None


def fetch_full_history_backward(client: Client, symbol: str, interval: str, limit: int = 1000) -> pd.DataFrame:
    """
    TELJES történeti letöltés, ha még nincs fájl:
    - Az UTOLSÓ 1000 gyertyát kéri le,
    - majd visszafelé lépeget (endTime = első_gyertya_open - 1) egészen a legrégebbi gyertyáig.
    - Végén időrendbe rakjuk (növekvő open_time).
    """
    batches = []
    end_time_ms = None
    total = 0
    while True:
        params = {'symbol': symbol, 'interval': interval, 'limit': limit}
        if end_time_ms is not None:
            params['endTime'] = end_time_ms
        kl = retry(client.get_klines, **params)
        if not kl:
            break
        df_batch = to_dataframe(kl)
        if df_batch.empty:
            break
        batches.append(df_batch)
        total += len(df_batch)
        # Következő lekérés határát a MOSTANI batch legelső gyertya elé tesszük
        first_open_ms = int(df_batch.index.min().timestamp() * 1000)
        if end_time_ms is not None and first_open_ms >= end_time_ms:
            break
        end_time_ms = first_open_ms - 1
        log(f"{symbol}: backfill... collected rows={total}, next endTime={pd.to_datetime(end_time_ms, unit='ms', utc=True)}")
        if len(kl) < limit:
            break
        time.sleep(0.2)

    if not batches:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume', 'close_time'])
    # A Binance a batch-eket legújabbról régebbi felé adta; fordítsuk vissza időrendbe, és egyesítsük
    df_all = pd.concat(reversed(batches))
    df_all = df_all[~df_all.index.duplicated(keep='last')].sort_index()
    return df_all


def fetch_incremental_forward(client: Client, symbol: str, interval: str, existing: pd.DataFrame, limit: int = 1000) -> pd.DataFrame:
    """
    Inkrementális FRISSÍTÉS olyan logikával, hogy:
    - a fájlból beolvasott utolsó gyertyát ELHAGYJA,
    - majd ATTÓL az open_time-tól kezdve újra lekér (így az a gyertya frissül),
    - és hozzáadódnak a legújabb gyertyák (beleértve az épp formálódót is).
    """
    if existing is None or existing.empty:
        return fetch_full_history_backward(client, symbol, interval, limit=limit)

    last_open = existing.index.max()
    kept = existing.iloc[:-1].copy() if len(existing) > 1 else existing.iloc[0:0].copy()

    start_ms = int(last_open.timestamp() * 1000)  # NEM +1 ms → a ledobott utolsót is újra kérjük
    df_all = kept
    last_guard = None
    while True:
        params = {'symbol': symbol, 'interval': interval, 'limit': limit, 'startTime': start_ms}
        kl = retry(client.get_klines, **params)
        if not kl:
            break

        df_batch = to_dataframe(kl)
        if df_batch.empty:
            break

        last_open_ms = int(df_batch.index.max().timestamp() * 1000)
        if last_guard is not None and last_open_ms <= last_guard:
            break
        last_guard = last_open_ms

        if df_all is None or df_all.empty:
            df_all = df_batch
        else:
            df_all = pd.concat([df_all, df_batch])
            df_all = df_all[~df_all.index.duplicated(keep='last')].sort_index()

        if len(kl) < limit:
            break
        start_ms = last_open_ms + 1
        time.sleep(0.2)

    return df_all if df_all is not None else kept

# ---------- Deprecated-kezeléshez kapcsolódó segédfüggvények ----------

def _deprecated_dir(outdir: Path) -> Path:
    d = outdir / "deprecated"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _parquet_basename(symbol: str, interval: str) -> str:
    return f"{symbol}_{interval}_ohlcv_data.parquet"

def _parquet_path(outdir: Path, symbol: str, interval: str) -> Path:
    return outdir / _parquet_basename(symbol, interval)

def move_parquet_to_deprecated(outdir: Path, symbol: str, interval: str, *, overwrite: bool = True) -> bool:
    """Átteszi a symbol parquetjét a deprecated almappába. Visszatér: sikerült-e ténylegesen mozgatni."""
    src = _parquet_path(outdir, symbol, interval)
    if not src.exists():
        log(f"[DEPRECATE] {symbol}: nincs parquet ({src.name}), nincs teendő.")
        return False

    dst_dir = _deprecated_dir(outdir)
    dst = dst_dir / src.name
    if dst.exists() and not overwrite:
        # ha nem írhatunk rá, akkor időbélyeggel új név
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dst = dst_dir / f"{ts}_{src.name}"

    os.replace(src, dst)  # atomi csere/mozgatás
    log(f"[DEPRECATE] {symbol}: áthelyezve → {dst.relative_to(outdir)}")
    return True

def list_active_symbols_from_exchange(client, quote: str | None) -> set[str]:
    """Azonnal használható aktív spot szimbólumok halmaza (TRADING), opcionális quote szűréssel."""
    info = retry(client.get_exchange_info)
    active = set()
    for s in info.get("symbols", []):
        # Binance-permissions: új API-kban a 'permissions' lehet list, de a régi 'isSpotTradingAllowed' is jelző
        is_spot = "SPOT" in s.get("permissions", []) or s.get("isSpotTradingAllowed", True)
        if s.get("status") == "TRADING" and is_spot:
            sym = s["symbol"]
            if quote and not sym.endswith(quote):
                continue
            active.add(sym)
    return active

def scan_local_parquet_symbols(outdir: Path, interval: str) -> set[str]:
    """Végigszkenneli az outdir-t az adott intervalhoz tartozó parquetekre és visszaadja a bennük szereplő szimbólumokat."""
    pat = re.compile(rf"^(?P<sym>[A-Z0-9]+)_{re.escape(interval)}_ohlcv_data\.parquet$", re.IGNORECASE)
    syms = set()
    for pth in outdir.glob(f"*_{interval}_ohlcv_data.parquet"):
        m_ = pat.match(pth.name)
        if m_:
            syms.add(m_.group("sym").upper())
    return syms

def deprecate_missing_pairs(outdir: Path, interval: str, live_symbols: set[str], *, only_check: set[str] | None = None) -> list[str]:
    """
    Deprecated-be mozgatja azokat a lokális CSV-ket, amelyek nincsenek a live listában.
    - only_check=None → az outdir-ben talált ÖSSZES CSV-t nézi az adott intervalon
    - only_check={...} → csak ezeket a megadott szimbólumokat ellenőrzi (pl. --pairs esetén)
    Visszaadja a ténylegesen deprecated-be mozgatott szimbólumok listáját.
    """
    local_syms = scan_local_parquet_symbols(outdir, interval) if only_check is None else {s.upper() for s in only_check}
    moved = []
    for sym in sorted(local_syms):
        if sym not in live_symbols:
            if move_parquet_to_deprecated(outdir, sym, interval):
                moved.append(sym)
    return moved


# --- Általános, atomikus szövegfájl-írás (manifesthez) ---
def _atomic_write_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), suffix='.tmp', encoding='utf-8', newline='') as tmp:
        tmp_name = tmp.name
        tmp.write(text)
    os.replace(tmp_name, path)


def _interval_to_timedelta(interval: str) -> timedelta:
    mult = {'m': 'minutes', 'h': 'hours', 'd': 'days', 'w': 'weeks'}
    unit = interval[-1].lower()
    num = int(interval[:-1])
    if unit not in mult:
        raise ValueError(f"Unsupported interval: {interval}")
    return timedelta(**{mult[unit]: num})


def update_manifest(manifest_path: Path,
                    symbol: str,
                    interval: str,
                    parquet_path: Path,
                    df_all: pd.DataFrame,
                    prev_len: int,
                    grace_seconds: int = 120) -> None:
    """Frissíti a manifest.json-t: páronként utolsó open_time, sorok, következő frissíthetőség."""
    now = datetime.now(timezone.utc)
    # parquet formátum: "YYYY-MM-DD HH:MM:SS+00:00"
    now_str = now.strftime("%Y-%m-%d %H:%M:%S%z")
    now_str = now_str[:-2] + ":" + now_str[-2:]  # "+0000" → "+00:00"

    # utolsó gyertya open_time (tz-aware UTC)
    last_open = df_all.index.max().to_pydatetime()
    if last_open.tzinfo is None:
        last_open = last_open.replace(tzinfo=timezone.utc)
    last_open_str = last_open.strftime("%Y-%m-%d %H:%M:%S%z")
    last_open_str = last_open_str[:-2] + ":" + last_open_str[-2:]

    rows_total = len(df_all)
    rows_added = max(rows_total - (prev_len or 0), 0)

    td = _interval_to_timedelta(interval)
    next_refresh_after = (last_open + td + timedelta(seconds=grace_seconds))
    next_refresh_after_str = next_refresh_after.strftime("%Y-%m-%d %H:%M:%S%z")
    next_refresh_after_str = next_refresh_after_str[:-2] + ":" + next_refresh_after_str[-2:]

    # meglévő manifest betöltése (ha van)
    data: Dict = {}
    try:
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding='utf-8'))
    except Exception:
        data = {}

    if not data:
        data = {
            "version": 1,
            "generated_at": now_str,
            "interval": interval,
            "pairs": {}
        }
    else:
        data["generated_at"] = now_str
        data["interval"] = interval
        if "pairs" not in data:
            data["pairs"] = {}

    data["pairs"][symbol] = {
        "parquet": str(parquet_path),
        "last_open_time": last_open_str,
        "rows_total": rows_total,
        "rows_added": rows_added,
        "updated_at": now_str,
        "next_refresh_after": next_refresh_after_str
    }

    _atomic_write_text(json.dumps(data, ensure_ascii=False, indent=2), manifest_path)


def fetch_pair(client: Client, symbol: str, interval: str, outdir: Path, limit: int = 1000,
               manifest_path: Path = None, grace_seconds: int = 120) -> int:
    """
    Egy szimbólum OHLCV adatainak letöltése és mentése parquetbe.
    - Ha VAN parquet: előrefelé inkrementális frissítés (az utolsó gyertyát eldobja és újrakéri)
    - Ha NINCS parquet: TELJES történeti backfill (visszafelé paginálás a legelső gyertyáig)
    - Mentés után manifest frissítés atomikusan
    """
    out_path = outdir / f"{symbol}_{interval}_ohlcv_data.parquet"
    existing = load_existing_parquet(out_path)
    prev_len = len(existing) if (existing is not None and not existing.empty) else 0

    if existing is not None and not existing.empty:
        log(f"{symbol}: updating forward from {existing.index.max()} (refresh last included)...")
        df_all = fetch_incremental_forward(client, symbol, interval, existing, limit=limit)
    else:
        log(f"{symbol}: fresh FULL history download (backfilling)...")
        df_all = fetch_full_history_backward(client, symbol, interval, limit=limit)

    if df_all is None or df_all.empty:
        log(f"{symbol}: no data returned.")
        return 0

    atomic_write_parquet(df_all, out_path)

    # MANIFEST frissítés
    if manifest_path is not None:
        try:
            update_manifest(manifest_path, symbol, interval, out_path, df_all, prev_len, grace_seconds)
        except Exception as e:
            log(f"Warning: manifest update failed for {symbol}: {e}")

    log(f"{symbol}: done. rows={len(df_all)} -> {out_path}")
    return len(df_all)



def main():
    parser = argparse.ArgumentParser(description="Binance OHLCV downloader (cron-safe)")
    parser.add_argument("--interval", default="4h", help="Binance interval, pl. 1m,5m,15m,1h,4h,1d (alapértelmezett: 4h)")
    parser.add_argument("--quote", default="USDT", help="Quote asset szűréshez (alapértelmezett: USDT)")
    parser.add_argument("--pairs", default="", help="Vesszővel elválasztott szimbólumok (pl. BTCUSDT,ETHUSDT). Ha üres, az összes USDT-s pár.")
    parser.add_argument("--outdir", default="data/ohlcv", help="Kimeneti mappa a CSV-knek (alapértelmezett: data/ohlcv)")
    import tempfile as _tf
    parser.add_argument("--lockfile", default=str(Path(_tf.gettempdir())/"crypto_ohlcv_downloader.lock"), help="Lock fájl útvonala (alap: rendszer temp könyvtár)")
    parser.add_argument("--manifest", default="", help="Manifest JSON útvonala (alapértelmezett: <outdir>/manifest.json)")
    parser.add_argument("--grace-seconds", type=int, default=120, help="Gyertya zárás utáni puffer másodpercben (manifest next_refresh_after-hoz)")
    args = parser.parse_args()

    t0 = time.perf_counter()
    outdir = Path(args.outdir)
    manifest_path = Path(args.manifest) if args.manifest else (outdir / "manifest.json")

    lock = LockFile(Path(args.lockfile))
    lock.acquire()
    try:
        client = make_client()

        # Élő (Binance) aktív spot szimbólumok lekérése. Ha nincs --pairs, akkor quote szerint szűrünk;
        # ha van --pairs, akkor a teljes univerzumban validálunk (quote nélkül).
        live_symbols = list_active_symbols_from_exchange(client, None if args.pairs.strip() else args.quote.upper())

        if args.pairs.strip():
            requested = {s.strip().upper() for s in args.pairs.split(",") if s.strip()}
            # MOZGATÁS deprecated-be a megadottak közül, ha nem élnek a Binance-en
            deprecated_now = deprecate_missing_pairs(outdir, args.interval, live_symbols, only_check=requested)
            # Feldolgozandó tényleges lista: a megadottak metszete az élőkkel
            symbols = sorted(requested - set(deprecated_now))
            if deprecated_now:
                log(f"[INFO] A következő megadott párok már nem elérhetők a Binance-en és deprecated-be kerültek: {', '.join(deprecated_now)}")
            if not symbols:
                log("[INFO] Nincs feldolgozható pár (--pairs mind deprecate-álva). Kilépés.")
                return 0
        else:
            # Nincs --pairs: quote szerint szűrt élő lista a feldolgozandó
            symbols = sorted(live_symbols)
            # Emellett az outdir-ben lévő, de már nem élő CSV-ket deprecated-be tesszük
            deprecated_now = deprecate_missing_pairs(outdir, args.interval, live_symbols, only_check=None)
            if deprecated_now:
                log(f"[INFO] Az alábbi lokális CSV-k már nem élő párokhoz tartoztak, deprecated-be kerültek: {', '.join(deprecated_now)}")

        if not symbols:
            log("No symbols to process. Exiting.")
            return 0

        total = 0
        for i, sym in enumerate(symbols, 1):
            log(f"[{i}/{len(symbols)}] Processing {sym} @ {args.interval}...")
            try:
                total += fetch_pair(client, sym, args.interval, outdir, limit=1000,
                                    manifest_path=manifest_path, grace_seconds=args.grace_seconds)
            except Exception as e:
                log(f"Error on {sym}: {type(e).__name__}: {e}. Skipping.")

        dt = time.perf_counter() - t0
        log(f"All done. Symbols processed: {len(symbols)}, total rows written (sum across files): {total}. Elapsed: {dt:.1f}s")
        return 0
    finally:
        lock.release()

if __name__ == "__main__":
    sys.exit(main())
