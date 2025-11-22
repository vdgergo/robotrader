Robotrader – Modular Crypto Strategy Engine
====================================================

Robotrader is a modular, backtest-ready cryptocurrency trading system designed to analyze multi-asset strategies using OHLCV data and sentiment (Fear & Greed Index), generate ranked trade signals, and execute simulated or live trades via Binance API.

----------------------------------------------------

📦 Main Modules
--------------

1. ohlcv_downloader.py – OHLCV Data Downloader
   - Downloads Binance candlestick data in a lock-safe and incremental way.
   - Supports full history and daily updates.
   - Saves data in Parquet format with manifest.json.
   - .env required: BINANCE_API_KEY, BINANCE_API_SECRET

2. fgi_downloader.py – Fear & Greed Index Downloader
   - Downloads CoinMarketCap FGI data (daily and 4h).
   - Checks and fills missing periods.
   - Saves in Parquet format.
   - .env required: CMC_API_KEY

3. analyzer/ – Signal Generation & Strategy Engine
   - strategy.py: Defines strategies with indicators and logic.
   - indicator.py: Loads indicator functions, supports caching.
   - analyzer_controller.py: Coordinates full signal pipeline.
   - Outputs:
     • pairs_for_buy.parquet
     • pairs_for_buy_filtered.parquet
     • pairs_for_buy_ranked.parquet

4. broker/ – Execution Engine
   - Simulates or executes trades on Binance.
   - Uses signal outputs from analyzer.
   - Tracks open and closed positions (Trade class).
   - Backtest mode supported.

----------------------------------------------------

🧪 Workflow
----------

1. Download OHLCV + FGI data
2. Run analyzer.py → generate signals
3. Run broker.py → process trades

----------------------------------------------------

⚙️ Configuration
---------------

All modules read from config/config.yaml:

- timeframe: "4h" or "1d", etc.
- strategies: name, indicators, pairs, logic
- filters: entry + pair filters
- quote: trading currency (e.g., USDT)
- max_threads: max concurrent trades
- initial_quote: initial capital for backtests

----------------------------------------------------

📄 Example Strategy Config (Dummy)
--------------------------

  - name: RSIwithEMA
    pairs: ["BTCUSDT", "ETHUSDT"]
    indicators: ["rsi", "ema"]
    signal_logic:
      buy: "(rsi > 40) and (ema > 10)"
      sell: "rsi <"

----------------------------------------------------

🔐 API Keys (.env format)
-------------------------

Place in config/myapi.env:

  BINANCE_API_KEY=your_key
  BINANCE_API_SECRET=your_secret
  CMC_API_KEY=your_cmc_key

----------------------------------------------------

🗃️ File Structure
------------------

data/
├── fgi/
│   ├── fear_greed_index.parquet
│   └── fear_greed_index_4h.parquet
├── ohlcv/
│   ├── BTCUSDT_4h_ohlcv_data.parquet
│   └── ...
├── signals/
│   ├── pairs_for_buy.parquet
│   ├── pairs_for_buy_filtered.parquet
│   ├── pairs_for_buy_ranked.parquet
└── cache/

----------------------------------------------------

📋 Logging
----------

Modules log to ./logs/ directory:

- analyzer.log
- broker_run.log
- coinmarketcap_fgi.downloader.log
- strategy.log, indicator.log etc.

----------------------------------------------------

🚀 Quick Start
--------------

1. Set API keys: cp config/myapi.env.example config/myapi.env
2. Run:
   python ohlcv_downloader.py
   python fgi_downloader.py
3. Run analyzer:
   python analyzer.py
4. Run backtest or broker:
   python broker.py

----------------------------------------------------

🚧 Project Status & Roadmap

CryptoTrader2024 is a work in progress. While the core components (data downloaders, analyzer, and broker logic) are functional, the system is currently limited to backtesting only.

✅ Implemented:

Full data ingestion (OHLCV + FGI)

Multi-strategy signal analysis

Backtest-ready trade logic

🔧 In Progress / Planned:

🔜 Real-time trading execution via Binance API

🔜 GUI or Web Dashboard for visualizing signals, trades, and strategy metrics

🔜 Risk management modules, performance reports, and optimization tools

This repository is under active development. 
