# Mappa- és modulszerkezet egy teljes "analyzer" modulhoz
# A meglévő letöltők (hystoric_crypto_data_downloader.py, coinmarketcap_fgi_downloader.py)
# által generált CSV-k feldolgozására, a Dashboard irányítása és a Broker kiszolgálása érdekében.

# Mappastruktúra:
# analyzer/
# ├── __init__.py
# ├── controller.py           --> AnalyzerController (belépési pont)
# ├── data_provider.py        --> MarketDataProvider (ohlcv + fgi)
# ├── strategy.py             --> Strategy, StrategyEngine
# ├── indicator.py            --> IndicatorCalculator, IndicatorFunctions
# ├── param_log.py            --> ParamTimeline (időbeli paraméterek)
# ├── filters.py              --> GlobalFilterSet, DashboardFilterSet
# ├── signal.py               --> Signal dataclass
# ├── backtester.py           --> Backtester (elméleti visszateszt)
# └── utils.py                --> Segédfüggvények (pl. időkerekítés, logolás)


# --- analyzer/controller.py ---
from .data_provider import MarketDataProvider
from .strategy import StrategyEngine
from .param_log import ParamTimeline
from .filters import GlobalFilterSet, DashboardFilterSet
from .signal import Signal

class AnalyzerController:
    def __init__(self, ohlcv_dir, fgi_path, config, dashboard_filters):
        self.market_data = MarketDataProvider(ohlcv_dir, fgi_path)
        self.strategy_engine = StrategyEngine(config["strategies"])
        self.param_log = ParamTimeline(config["param_history_path"])
        self.global_filters = GlobalFilterSet(config["global"])
        self.dashboard_filters = DashboardFilterSet(**dashboard_filters)

    def analyze_next_bar(self, current_time):
        df_fgi = self.market_data.get_fgi(current_time)
        pairs = self.dashboard_filters.filter_pairs(self.market_data.list_pairs())

        results = []
        for pair in pairs:
            if not self.global_filters.check_all(pair, current_time, df_fgi):
                continue
            ohlcv = self.market_data.load_ohlcv(pair, current_time)
            signals = self.strategy_engine.evaluate_all(
                pair=pair,
                time=current_time,
                ohlcv=ohlcv,
                fgi=df_fgi,
                param_source=self.param_log
            )
            results += signals

        sorted_signals = sorted(
            [s for s in results if s.action == "buy"],
            key=lambda s: s.score,
            reverse=True
        )

        return {
            "time": current_time,
            "signals": sorted_signals,
            "raw": results
        }


# --- analyzer/backtester.py ---
from .signal import Signal

class Backtester:
    def __init__(self, controller):
        self.controller = controller

    def run(self, start_time, end_time, step_hours=4):
        import pandas as pd
        from datetime import timedelta

        time = start_time
        all_signals = []

        while time <= end_time:
            result = self.controller.analyze_next_bar(time)
            all_signals.append(result)
            time += timedelta(hours=step_hours)

        return all_signals

# --- analyzer/data_provider.py ---
import pandas as pd
from pathlib import Path

class MarketDataProvider:
    def __init__(self, ohlcv_dir, fgi_path):
        self.ohlcv_dir = Path(ohlcv_dir)
        self.fgi_df = pd.read_csv(fgi_path, index_col=0, parse_dates=True)

    def list_pairs(self):
        return [p.stem.split("_")[0] for p in self.ohlcv_dir.glob("*_ohlcv_data.csv")]

    def load_ohlcv(self, pair, time):
        df = pd.read_csv(self.ohlcv_dir / f"{pair}_4h_ohlcv_data.csv", index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        return df.loc[:time].tail(100)  # utolsó 100 gyertya

    def get_fgi(self, time):
        return self.fgi_df.loc[self.fgi_df.index <= time].iloc[-1].to_dict()


# --- analyzer/strategy.py ---
from .indicator import IndicatorCalculator

class Strategy:
    def __init__(self, config):
        self.name = config["name"]
        self.pairs = config["pairs"]
        self.indicators = config["indicators"]

    def is_enabled_for_pair(self, pair):
        return pair in self.pairs

    def evaluate(self, ohlcv, fgi, time, params):
        ind_results = IndicatorCalculator.compute_indicators(ohlcv, self.indicators, params)
        # Dummy példa: csak akkor javasol BUY-t, ha wavet > 0 és fgi > 40
        if ind_results.get("wavet", pd.Series()).iloc[-1] > 0 and fgi["fear_greed_value"] > 40:
            return [Signal(pair=ohlcv.name, action="buy", score=0.8, strategy=self.name, time=time)]
        return []

class StrategyEngine:
    def __init__(self, strategy_configs):
        self.strategies = [Strategy(cfg) for cfg in strategy_configs]

    def evaluate_all(self, pair, time, ohlcv, fgi, param_source):
        results = []
        for strategy in self.strategies:
            if strategy.is_enabled_for_pair(pair):
                params = param_source.get_params(strategy.name, time)
                ohlcv.name = pair  # a jelhez kész
                results += strategy.evaluate(ohlcv, fgi, time, params)
        return results


# --- analyzer/indicator.py ---
import pandas as pd

class IndicatorCalculator:
    @staticmethod
    def compute_indicators(ohlcv, indicators, params):
        result = {}
        for ind in indicators:
            name = ind["name"]
            p = params.get(name, {})
            if name == "wavet":
                result["wavet"] = ohlcv["close"].rolling(p.get("length", 10)).mean()  # dummy
            elif name == "rsi":
                delta = ohlcv["close"].diff()
                gain = delta.where(delta > 0, 0)
                loss = -delta.where(delta < 0, 0)
                avg_gain = gain.rolling(p.get("period", 14)).mean()
                avg_loss = loss.rolling(p.get("period", 14)).mean()
                rs = avg_gain / avg_loss
                result["rsi"] = 100 - (100 / (1 + rs))
        return result


# --- analyzer/param_log.py ---
import pandas as pd

class ParamTimeline:
    def __init__(self, history_path):
        self.df = pd.read_csv(history_path, parse_dates=["time"])
        self.df["time"] = pd.to_datetime(self.df["time"], utc=True)

    def get_params(self, strategy_name, time):
        df = self.df[(self.df["strategy"] == strategy_name) & (self.df["time"] <= time)]
        if df.empty:
            return {}
        latest = df.sort_values("time").iloc[-1]
        return eval(latest["params"])


# --- analyzer/filters.py ---
class GlobalFilterSet:
    def __init__(self, config):
        self.fgi_min = config.get("fgi_min", 0)

    def check_all(self, pair, time, fgi):
        return fgi["fear_greed_value"] >= self.fgi_min

class DashboardFilterSet:
    def __init__(self, only=None, exclude=None):
        self.only = set(only) if only else set()
        self.exclude = set(exclude) if exclude else set()

    def filter_pairs(self, pairs):
        return [p for p in pairs if (not self.only or p in self.only) and p not in self.exclude]


# --- analyzer/signal.py ---
from dataclasses import dataclass
import pandas as pd

@dataclass
class Signal:
    pair: str
    action: str  # "buy" vagy "sell"
    score: float
    strategy: str
    time: pd.Timestamp
