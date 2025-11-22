import pandas as pd
import datetime as dt
import pytz

class Pair:
    _instances = {}
    def __init__(self, symbol, data_provider):
        """
        symbol: pl. 'BTCUSDT'
        ohlcv: DataFrame ['open','high','low','close','volume'] datetime index-szel
        """

        if symbol in Pair._instances:
            raise ValueError(f"Hiba! {symbol} már létrehozásra került egy másik stratégiánál.")

        self.symbol = symbol
        self.data_provider = data_provider
        self.indicator_results = {}   # kiszámolt indikátorok
        self.signals = None  # buy/sell jelzések
        Pair._instances[symbol] = self

    @classmethod
    def get_pair_by_attribute(cls, attr, value):
        """
        Megkeres egy pár példányt egy attribútum értéke alapján.
        Visszaadja az első találatot, vagy None-t, ha nincs ilyen.
        
        Példa: Pair.get_pair_by_attribute('symbol', 'BTCUSDT')
        """
        for pair_instance in cls._instances.values():
            if hasattr(pair_instance, attr) and getattr(pair_instance, attr) == value:
                return pair_instance
        return None