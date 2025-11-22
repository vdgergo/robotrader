# run_broker.py
from shared.config_loader import load_config
from broker.trade import Trade
from shared.data_provider import MarketDataProvider
from broker.broker_controller import Broker
from shared.tools import interval_to_timedelta

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytz
import pdb
import logging

# --- Logging beállítás ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

log_dir_path = Path('logs')
log_dir_path.mkdir(parents=True, exist_ok=True)
file_handler = logging.FileHandler(log_dir_path / 'broker_run.log')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

logger.addHandler(file_handler)

# Konfig betöltés - A szkript saját mappájához képest keressük a config.yaml fájlt, hogy bárhonnan futtatható legyen.
config_path = Path(__file__).parent / 'config/config.yaml'
config = load_config(config_path)

# Adatszolgáltató példány
data_provider = MarketDataProvider(config)

# Broker inicializálás
broker = Broker(
    config=config,
    data_provider=data_provider,
    backtest=True  # Backtest mód bekapcsolása
    )

# Iteráció a megadott dátumtartományon
start_date = datetime(2025, 1, 1, tzinfo=pytz.UTC)
end_date = datetime(2025, 11, 19, tzinfo=pytz.UTC)
current_time = start_date
time_step = interval_to_timedelta(config['timeframe'])

while current_time <= end_date:
    print(f"--- Futtatás a következő időpontra: {current_time.strftime('%Y-%m-%d %H:%M')} ---")
    broker.trade_by_latest_closed_bar(current_time)
    current_time += time_step


# A backtest végén mentjük az aktív és lezárt trade-eket
logger.info("Backtest befejeződött. Mentjük a végső trade állapotokat.")
broker.data_provider.save_trades(Trade.get_trades_as_dict())
print(f"\nBacktest vége. Összesen {len(broker.closed_trades_df)} lezárt trade kerül mentésre.")
print("A closed_trades_df utolsó 5 sora mentés előtt:")
print(broker.closed_trades_df.tail())
broker.data_provider.save_closed_trades(broker.closed_trades_df)

print("Broker futtatása befejeződött.")
