from datetime import datetime, timezone, timedelta
from shared.tools import floor_to_nh, interval_to_timedelta
from analyzer.strategy import Strategy
from pathlib import Path

import pytz
from datetime import datetime, timezone, timedelta

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
file_handler = logging.FileHandler(log_dir_path / 'controller.log')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

class AnalyzerController:
    def __init__(self, config,data_provider,strategies):
        self.config = config
        self.data_provider = data_provider
        self.strategies = strategies
        self.filters = self.config['filters']
        self.ranking_method = self.config['ranking_method']
        self.timeframe = self.config['timeframe']
        self.timeframe_delta = interval_to_timedelta(self.config['timeframe'])
        logger.debug(f"AnalyzerController initialized with timeframe: {self.timeframe}")


    def analyze_latest_bar(self):
        #teszt jelleggel egy adott időpontot kap bemenetként
        actual_time =(datetime.now(tz=timezone.utc)) #2025,10,6,16,53,tzinfo=pytz.UTC
        latest_closed_bar_open_time = floor_to_nh(actual_time,4) - self.timeframe_delta
        latest_closed_bar_close_time = latest_closed_bar_open_time + self.timeframe_delta - timedelta(milliseconds=1)
        logger.debug(f"analyze_latest_bar() called at actual time: {actual_time} and run on latest closed bar ({latest_closed_bar_open_time} - {latest_closed_bar_close_time}) at {self.timeframe} timeframe.")
        logger.info(f"Analyzing latest bar [{latest_closed_bar_open_time.strftime('%Y-%m-%d %H:%M')} - {latest_closed_bar_close_time.strftime('%Y-%m-%d %H:%M')}] at {actual_time} on {self.timeframe} timeframe.")
        
        Strategy.pairs_with_ohlcv_data = self.data_provider.detect_available_pairs()

        # 1. Ciklus: Stratégiák alapbeállítása és a párok előszűrése
        for name, strategy in self.strategies.items():
            strategy.timeframe = self.timeframe
            strategy.timeframe_delta = self.timeframe_delta
            strategy.latest_closed_bar_open_time = latest_closed_bar_open_time
            strategy.apply_pair_filters(self.filters['pair_filters'])

        # 2. Az összes stratégia párszűrése adat-elérhetőség alapján
        Strategy.check_pair_ohlcv_availability()

        # 3. Ciklus: Tényleges számítások a már megszűrt párokkal és párok létrehozása
        for name, strategy in self.strategies.items():
            strategy.create_pairs()
            strategy.run_all_indicators()
            strategy.run_logic()
        
        Strategy.merge_strategies(self.data_provider)
        Strategy.apply_entry_filters(self.data_provider, self.filters['entry_filters'], latest_closed_bar_open_time)
        Strategy.apply_sorting(self.data_provider, self.ranking_method, latest_closed_bar_open_time)
        logger.info(f"Analyze end. All results saved to files.")
