from shared.tools import floor_to_nh, interval_to_timedelta
from broker.trade import Trade
from pathlib import Path
from dotenv import load_dotenv
import json
import os
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException
from datetime import datetime
import pandas as pd
import logging

# import pytz
# from datetime import datetime, timezone, timedelta

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
file_handler = logging.FileHandler(log_dir_path / 'controller.log')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

def create_binance_client():
    """Létrehoz és visszaad egy Binance kliens objektumot a .env fájlban lévő kulcsok alapján."""
    # A .env fájlt a projekt gyökérkönyvtárában keressük, ami egy szinttel feljebb van a 'broker' mappától.
    # Ez a megoldás független attól, hogy honnan futtatjuk a szkriptet.
    project_root = Path(__file__).parent.parent
    env_path = project_root / 'config/myapi.env'
    load_dotenv(dotenv_path=env_path, override=True)

    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        logger.error("A BINANCE_API_KEY és BINANCE_API_SECRET változókat be kell állítani a myapi.env fájlban.")
        raise ValueError("Hiányzó API kulcsok.")

    return Client(api_key, api_secret)

class Broker:
    def __init__(self, config, data_provider, backtest=False):
        self.config = config
        self.data_provider = data_provider
        self.base_currency = config['quote']
        self.is_backtest = backtest
        self.initial_quote = config['initial_quote']
        self.max_threads = config["max_threads"]
        self.timeframe = config["timeframe"]
        self.timeframe_delta = interval_to_timedelta(config["timeframe"])
        self.states_df = data_provider.load_cache('signals','pairs_for_buy_filtered.parquet') # 'pairs_for_buy_filtered.parquet'
        self.ranked_entrys = data_provider.load_cache('signals','pairs_for_buy_ranked.parquet')
        self.client = client = create_binance_client()
        self.get_exchange_info = self.client.get_exchange_info()
        self.rate_limits = self.get_exchange_info['rateLimits']

        # A Trade osztály informálása a maximális szálakról
        Trade.max_trades = self.max_threads

        # Korábban lezárt trade-ek betöltése
        self.closed_trades_df = self.data_provider.load_closed_trades()

        # Meglévő trade-ek betöltése a DataProvider-en keresztül
        loaded_trades = self.data_provider.load_trades()
        if loaded_trades:
            Trade.load_from_dict(loaded_trades)

    def get_signals_for_bar(self, actual_time):
        """
        Beolvassa a legfrissebb állapotot és rangsort minden párhoz a legutolsó gyertya alapján.

        Returns:
            pd.DataFrame: Egy DataFrame, amelynek indexe a szimbólum,
                          és oszlopai a 'state' és 'rank'.
                          Példa:
                                 state  rank
                          symbol
                          BTCUSDT    BUY   1.0
                          ETHUSDT    OUT   NaN
        """
        latest_closed_bar_open_time = floor_to_nh(actual_time,4) - self.timeframe_delta
        logger.info(f"TESZT: Jelek lekérése a következő időponthoz: {latest_closed_bar_open_time}")

        if self.states_df.empty:
            logger.warning("A states_df üres, nem lehet a legfrissebb jeleket kinyerni.")
            return pd.DataFrame(columns=['state', 'rank'])

        try:
            latest_states = self.states_df.loc[latest_closed_bar_open_time].rename('state')
            latest_ranks = self.ranked_entrys.loc[latest_closed_bar_open_time].rename('rank') if not self.ranked_entrys.empty and latest_closed_bar_open_time in self.ranked_entrys.index else pd.Series(name='rank', dtype=float)
        except KeyError:
            logger.warning(f"Nincs adat a {latest_closed_bar_open_time} időponthoz. Üres DataFrame-mel tér vissza.")
            return pd.DataFrame(columns=['state', 'rank'])

        signals_df = pd.concat([latest_states, latest_ranks], axis=1)
        signals_df.index.name = 'symbol'

        # Egyedi rendezési sorrend a 'state' oszlophoz, a NaN értékek a végére kerülnek
        state_order = ['BUY', 'HOLD', 'SELL', 'OUT']
        signals_df['state'] = pd.Categorical(signals_df['state'], categories=state_order, ordered=True)


        # Rendezés a 'state' (egyedi sorrend) és a 'rank' (növekvő) szerint
        signals_df = signals_df.sort_values(by=['state', 'rank'])

        logger.info(f"Legfrissebb jelek beolvasva {len(signals_df)} párhoz DataFrame-be.")
        if not self.is_backtest:
            self.data_provider.save_cache('signals','latest_signals.parquet',signals_df)
        return signals_df

    def _get_price_for_bar(self, symbol, bar_time):
        """Lekéri egy adott szimbólum záróárát egy adott gyertya időpontjához."""
        try:
            # Csak a 'close' oszlopot kérjük le a hatékonyság érdekében
            ohlcv = self.data_provider.get_ohlcv(symbol, start=bar_time, end=bar_time, columns=['close'])
            if not ohlcv.empty:
                return float(ohlcv['close'].iloc[0])
            else:
                logger.warning(f"Nem található OHLCV adat a(z) {symbol} párhoz a(z) {bar_time} időpontban. Az ár meghatározása sikertelen.")
                return None
        except Exception as e:
            logger.error(f"Hiba az ár lekérése közben ({symbol} @ {bar_time}): {e}")
            return None


    def _close_position(self, symbol, sell_time):
        """
        Lezár egy pozíciót a Binance-en és a belső nyilvántartásban.
        Jelenleg szimulálja az eladást és frissíti a Trade nyilvántartást.
        """
        trade_to_close = Trade.get_trade_by_attribute('symbol', symbol)
        if not trade_to_close:
            logger.error(f"Hiba: megpróbáltunk lezárni egy nem létező trade-et: {symbol}")
            return

        quantity_to_sell = trade_to_close.quantity
        logger.info(f"ELADÁSI MEGBÍZÁS KEZDEMÉNYEZÉSE: {quantity_to_sell:.6f} darab {symbol} eladása.")

        try:
            # --- TÉNYLEGES BINANCE API HÍVÁS HELYE ---
            # order = self.client.create_order(symbol=symbol, side=Client.SIDE_SELL, type=Client.ORDER_TYPE_MARKET, quantity=quantity_to_sell)
            # logger.info(f"Binance eladási megbízás sikeres: {order}")
            # sell_price = float(order['fills'][0]['price'])

            # Eladási ár meghatározása a historikus adatokból
            signal_bar_time = floor_to_nh(sell_time, 4) - self.timeframe_delta
            sell_price = self._get_price_for_bar(symbol, signal_bar_time)

            if sell_price is None:
                logger.error(f"Nem sikerült meghatározni az eladási árat a(z) {symbol} párhoz, a pozíció zárása megszakítva.")
                return

            # 1. Lezárt trade adatainak összegyűjtése naplózáshoz
            closed_trade_data = {
                'symbol': symbol,
                'buy_price': trade_to_close.buy_price,
                'buy_time': trade_to_close.buy_time,
                'sell_price': sell_price,
                'sell_time': sell_time,
                'quantity': quantity_to_sell,
                'pnl': (sell_price - trade_to_close.buy_price) * quantity_to_sell,
                'pnl_pct': (sell_price / trade_to_close.buy_price - 1) * 100
            }
            
            # Sikeres eladás után eltávolítjuk a pozíciót a nyilvántartásból
            if Trade.close_trade(symbol):
                # 2. Pozíció eltávolítása a belső nyilvántartásból sikeres volt,
                # mentsük a frissített aktív trade listát.
                # és a closed_trade-he adjuk hozzá a lezárt trade adatait
                self.closed_trades_df = pd.concat([self.closed_trades_df, pd.DataFrame([closed_trade_data])], ignore_index=True)
                if not self.is_backtest:
                    trades_to_save = Trade.get_trades_as_dict()
                    self.data_provider.save_trades(trades_to_save)
                    self.data_provider.save_closed_trades(self.closed_trades_df)
            else:
                # Ez elvileg nem fordulhat elő, ha a trade_to_close létezett, de biztonsági naplózásnak jó.
                logger.error(f"Konzisztencia hiba: a(z) {symbol} trade létezett, de a Trade.close_trade() False értékkel tért vissza.")

        except BinanceAPIException as e:
            logger.error(f"Hiba az eladási megbízás során ({symbol}): {e}")
        except Exception as e:
            logger.error(f"Váratlan hiba a pozíció zárásakor ({symbol}): {e}")

    def _open_position(self, symbol, rank, buy_time):
        """
        Pozíciót nyit a Binance-en és a belső nyilvántartásban.
        Jelenleg szimulálja a vásárlást és létrehoz egy új Trade példányt.
        """
        logger.info(f"VÉTELI MEGBÍZÁS KEZDEMÉNYEZÉSE: {symbol} (Rank: {rank})")
        
        # TODO: Mennyiség számításának logikája (pl. initial_quote / max_threads alapján)
        # Itt most egy fix 10 USDT értékkel szimulálunk
        try:
            # TODO: Ide kerül a tényleges Binance vételi API hívás
            # order = self.client.create_order(symbol=symbol, side=Client.SIDE_BUY, type=Client.ORDER_TYPE_MARKET, quoteOrderQty=10)
            # logger.info(f"Binance vételi megbízás sikeres: {order}")
            # buy_price = float(order['fills'][0]['price'])
            # quantity = float(order['executedQty'])

            # Vételi ár meghatározása a historikus adatokból
            buy_price = self._get_price_for_bar(symbol, buy_time-self.timeframe_delta)
            if buy_price is None:
                logger.error(f"Nem sikerült meghatározni a vételi árat a(z) {symbol} párhoz, a pozíció nyitása megszakítva.")
                return

            # Mennyiség számítása a szimulált 10 USDT alapján
            quantity = 10 / float(buy_price)

            # Új Trade példány létrehozása a sikeres vásárlás után
            Trade(symbol=symbol, buy_price=buy_price, buy_time=buy_time, quantity=quantity)
            if not self.is_backtest:
                trades_to_save = Trade.get_trades_as_dict()
                self.data_provider.save_trades(trades_to_save) # Trade-ek mentése

        except (BinanceAPIException, ValueError) as e:
            logger.error(f"Hiba a vételi megbízás vagy a Trade létrehozása során ({symbol}): {e}")
        except Exception as e:
            logger.error(f"Váratlan hiba a pozíció nyitásakor ({symbol}): {e}")

    def trade_by_latest_closed_bar(self,actual_time):
        signals_to_trade = self.get_signals_for_bar(actual_time)
        logger.info("Kereskedési jelek a legutolsó lezárt gyertyához:\n%s", signals_to_trade.head())

        # 2. Eladási logika: meglévő pozíciók ellenőrzése
        for symbol in list(Trade.active_trades.keys()):
            state = signals_to_trade.loc[symbol, 'state'] if symbol in signals_to_trade.index else 'NaN'
            if state in ['SELL', 'OUT'] or pd.isna(state):
                logger.info(f"Eladási jelzés ({state}) a nyitott pozícióra: {symbol}. Zárás kezdeményezése.")
                self._close_position(symbol, actual_time)

        # 3. Vételi logika: Új pozíciók nyitása a 'BUY' jelek alapján
        buy_signals = signals_to_trade[signals_to_trade['state'] == 'BUY']
        for symbol, row in buy_signals.iterrows():
            # Ellenőrizzük, hogy van-e még szabad szál
            if len(Trade.active_trades) >= self.max_threads:
                logger.info("Elértük a maximális pozíciószámot. Nincs több vétel.")
                break

            # Ellenőrizzük, hogy van-e már nyitott pozíció erre a párra
            if symbol in Trade.active_trades:
                logger.debug(f"Már van nyitott pozíció a {symbol} párhoz, kihagyva.")
                continue

            # Ha minden rendben, pozíció nyitása
            self._open_position(symbol, row['rank'], actual_time)
        pass
