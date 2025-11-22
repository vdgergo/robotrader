from pathlib import Path
import re
import pandas as pd
import json

import logging

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

log_dir_path = Path('logs')
log_dir_path.mkdir(parents=True, exist_ok=True)
file_handler = logging.FileHandler(log_dir_path / 'data_provider.log')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

class MarketDataProvider:
    def __init__(self, config):
        self.ohlcv_dir = Path(config['directories']['ohlcv_dir'])
        self.fgi_dir = Path(config['directories']['fgi_dir'])
        self.param_dir = Path(config['directories']['param_dir'])
        self.cache_dir = Path(config['directories']['cache_dir'])
        self.signals_dir = Path(config['directories']['signals_dir'])
        self.trades_dir = Path(config['directories']['trades_dir'])
        self.timeframe = config['timeframe']
        logger.debug(f"MarketDataProvider initialized with timeframe: {self.timeframe}")

    def detect_available_pairs(self):
        """
        Végigszkenneli az ohlcv mappát és visszaadja, hogy mely szimbólumokról van adat
        az adott timeframe (interval) szerint.
        """
        pattern = re.compile(
            rf"^(?P<sym>[A-Z0-9]+)_{re.escape(self.timeframe)}_ohlcv_data\.parquet$", 
            re.IGNORECASE
        )
        syms = set()
        for file in self.ohlcv_dir.glob(f"*_{self.timeframe}_ohlcv_data.parquet"):
            m = pattern.match(file.name)
            if m:
                syms.add(m.group("sym").upper())
        syms = sorted(syms)
        self.pairs_list = syms
        logger.debug(f"Detected pairs in ohlcv directory: {', '.join(syms)}")

        return syms
        
    def load_strategy_param_table(self, filename):
        path = self.param_dir / filename
        try:
            df = pd.read_parquet(path, engine="pyarrow")
            df = df.where(pd.notnull(df), None)
            return df

        except:
            logger.warning(f"Parameter file not found for: {filename}")
            return None
        
    def save_strategy_param_table(self, filename, df):
        path = self.param_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, engine='pyarrow',index=True)
        logger.debug(f"Saved strategy parameter table to '{path}'.")

    def get_ohlcv(self, pair, start=None, end=None, columns=None):
        filename = f"{pair}_{self.timeframe}_ohlcv_data.parquet"
        path = self.get_full_path('ohlcv',filename)
        logger.debug(f"Loading OHLCV data for '{pair}' from '{path}'.")
        filters = [] if start or end else None
        if start is not None:
            filters.append(('open_time','>=',start))
        if end is not None:
            filters.append(('open_time','<=',end))        
        
        df = pd.read_parquet(path, engine="pyarrow", columns=columns, filters=filters)
        return df

    def cache_exists(self, data_type, filename):
        path = self.get_full_path(data_type,filename)
        return path.exists()

    def load_cache(self, data_type, filename,start=None, end=None, columns=None):
        path = self.get_full_path(data_type,filename)
        logger.debug(f"Loading cache type '{data_type}' from '{path}'.")
        filters = [] if start or end else None
        if start is not None:
            filters.append(('open_time','>=',start))
        if end is not None:
            filters.append(('open_time','<=',end))  
        
        logger.debug(f"Filters: {filters}, start: {start}, end: {end}, columns: {columns}")
        df = pd.read_parquet(path, engine="pyarrow", columns=columns, filters=filters)
        return df

    def save_cache(self, data_type, filename, df):
        path = self.get_full_path(data_type,filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, engine='pyarrow',index=True)
        logger.debug(f"Saved cache type '{data_type}' to '{path}'.")

       
    def load_and_check_old_cached_data(self, data_type, filename):  
        """
        Betölti és ellenőrzi az előzőleg elmentett (cache-elt) adatokat.

        Cache akkor használható, ha:
            - a fájl létezik,
            - nem üres

        Paraméterek:
        -----------
        filename : str
            A cache-elt adatot tartalmazó fájl neve.

        Visszatér:
        ---------
        Tuple:
            - cache : bool → használható-e cache
            - cached_signals : pd.DataFrame vagy False
            - cached_from : pd.Timestamp vagy False → honnantól szükséges újraszámolni
        """
        if self.cache_exists(data_type, filename):
            loaded_cache = self.load_cache(data_type, filename)
        else:
            logger.debug(f"Cache file not found: {filename}")
            return (False, False, False)

        if loaded_cache.empty:
            logger.debug(f"Cache file exists but is empty: {filename}")
            return (False, False, False)

        cache = True
        calculate_from = loaded_cache.index[-1] 

        return (cache, loaded_cache, calculate_from)

    def save_trades(self, trades_data):
        """Elmenti a megadott trade adatokat a beállított JSON fájlba."""
        path = self.get_full_path('trades', 'active_trades.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, 'w') as f:
                json.dump(trades_data, f, indent=4)
            logger.info(f"{len(trades_data)} trade sikeresen elmentve ide: {path}")
        except IOError as e:
            logger.error(f"Hiba a trade-ek fájlba írása közben: {e}")

    def load_trades(self):
        """Beolvassa a trade-eket a JSON fájlból és visszaadja szótárként."""
        path = self.get_full_path('trades', 'active_trades.json')
        if not path.exists():
            logger.info("Nincs mentett trade fájl, vagy az útvonal nincs beállítva. Üres trade listával indulunk.")
            return {}

        try:
            with open(path, 'r') as f:
                loaded_trades = json.load(f)
            logger.info(f"{len(loaded_trades)} trade adat beolvasva a(z) {path} fájlból.")
            return loaded_trades
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Hiba a trade fájl betöltése közben: {e}. A fájl lehet, hogy sérült. Üres trade listával indulunk.")
            return {}

    def save_closed_trades(self, closed_trades_df: pd.DataFrame):
        """Elmenti a lezárt trade-ek teljes DataFrame-jét a Parquet fájlba."""
        path = self.get_full_path('trades', 'closed_trades.parquet')
        path.parent.mkdir(parents=True, exist_ok=True)

        if not closed_trades_df.empty:
            # Duplikátumok eltávolítása mentés előtt, hogy garantáljuk az adatintegritást.
            # A 'buy_time' és 'symbol' együttesen egyedi azonosítóként szolgál.
            original_rows = len(closed_trades_df)
            closed_trades_df = closed_trades_df.drop_duplicates(subset=['symbol', 'buy_time'], keep='last')
            logger.info(f"{original_rows - len(closed_trades_df)} duplikált trade eltávolítva mentés előtt.")

        try:
            closed_trades_df.to_parquet(path, index=False)
            logger.info(f"Lezárt trade-ek naplója frissítve: {path}. Összesen {len(closed_trades_df)} trade.")
        except Exception as e:
            logger.error(f"Hiba a lezárt trade-ek naplózása közben: {e}")

    def load_closed_trades(self) -> pd.DataFrame:
        """Beolvassa a lezárt trade-ek naplóját. Ha nem létezik, üres DataFrame-et ad vissza."""
        path = self.get_full_path('trades', 'closed_trades.parquet')
        if not path.exists():
            logger.info("Nem található lezárt trade-ek naplófájl, üres listával indulunk.")
            return pd.DataFrame()
        
        try:
            df = pd.read_parquet(path)
            logger.info(f"{len(df)} lezárt trade betöltve a(z) {path} fájlból.")
            return df
        except Exception as e:
            logger.error(f"Hiba a lezárt trade-ek naplójának betöltése közben: {e}. Üres listával indulunk.")
            return pd.DataFrame()

    def get_full_path(self, data_type,filename):
        if data_type == 'cache':
            dir_path = self.cache_dir
        elif data_type == "signals":
            dir_path = self.signals_dir
        elif data_type == 'fgi':
            dir_path = self.fgi_dir
        elif data_type == 'ohlcv':
            dir_path = self.ohlcv_dir
        elif data_type == 'trades':
            dir_path = self.trades_dir


        path = dir_path / filename
        return path
