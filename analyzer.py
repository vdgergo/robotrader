from shared.config_loader import load_config
from analyzer.analyzer_controller import AnalyzerController
from shared.data_provider import MarketDataProvider
from analyzer.strategy import Strategy
from pathlib import Path

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
logger.setLevel(logging.INFO)
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

log_dir_path = Path('logs')
log_dir_path.mkdir(parents=True, exist_ok=True)
file_handler = logging.FileHandler(log_dir_path / 'analyzer.log')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

# --- Fő program ---

# 1. Konfiguráció betöltése fájlból
# A szkript saját mappájához képest keressük a config.yaml fájlt, hogy bárhonnan futtatható legyen.
config_path = Path(__file__).parent / 'config/config.yaml'
config = load_config(config_path)

#. 2. adatbetöltő létrehozása a megadott mappákkal
data_provider = MarketDataProvider(config)
#. 3. stratégiák példányosítása configból
strategies = {s['name'] : Strategy(**s,data_provider=data_provider) for s in config['strategies']}
analyzer = AnalyzerController(
    config=config,
    data_provider=data_provider,
    strategies = strategies
    )

analyzer.analyze_latest_bar()
