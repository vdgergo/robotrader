from analyzer.indicator import Indicator
from analyzer.pair import Pair
import pandas as pd
import numpy as np
import ast
from shared.tools import load_function, get_default_params, generate_params_hash, is_all_float_zero
from itertools import product as itertools_product
from functools import reduce
from pathlib import Path

import pdb

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
file_handler = logging.FileHandler(log_dir_path / 'strategy.log')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)


class Strategy():
    _instances = {}
    pairs_with_ohlcv_data = [] # ohlcv mappa gyökerében elérhető párok. Itt csak azok szerepelnek amiről az utolsó futtatáskor volt adat.
    active_strategy_pairs = set() # itt a stratégiákban megadott párok vannak összeszedve és ki van szelektálva az ami az all_active_pairs-ben nem szerepel (nincs rá adat)

    def __init__(self,name,pairs,indicators,signal_logic,data_provider):
        Strategy._instances[name] = self
        self.data_provider = data_provider
        self.name = name
        self.pair_names = list(dict.fromkeys(pairs))
        self.signal_logic = signal_logic
        self.indicator_names = indicators
        self.indicators = {ind_name: Indicator(ind_name,self.data_provider) for ind_name in self.indicator_names}
        logger.debug(f"{name} strategy was created with {', '.join(self.indicator_names).upper()} indicators for {', '.join(self.pair_names)}")

    @classmethod
    def apply_pair_filters(cls,pfilter):
        only = [x for x in pfilter['only'] if x in cls.pairs_with_ohlcv_data] # ez garantálja, hogy olyan ne kerüljön be a párok közé amire nincs adat (pl vételen elütés miatt ne jöjjön léltre egy fiktív pár)
        exl = pfilter['exclude']

        for name, strategy_instance in cls._instances.items():
            if hasattr(strategy_instance, 'pair_names') and strategy_instance.pair_names:
                logger.debug(f"Applying pair filters for strategy '{name}'. Initial pairs: {len(strategy_instance.pair_names)}")
                if only != []:                    
                    remained_pairs = [x for x in strategy_instance.pair_names if x in only]
                    filtered_out_pairs = set(strategy_instance.pair_names) - set(remained_pairs)
                    if filtered_out_pairs:
                        logger.info(f"Pair names filtered out by 'only' filter: {', '.join(filtered_out_pairs)}")
                    strategy_instance.pair_names = remained_pairs
                    logger.debug(f"Remained pairs names after apply 'only' filter on '{name}': {len(strategy_instance.pair_names)}")

                if exl != []:
                    strategy_instance.pair_names = [x for x in strategy_instance.pair_names if x not in exl]

                    remained_pairs = [x for x in strategy_instance.pair_names if x not in exl]
                    filtered_out_pairs = set(strategy_instance.pair_names) - set(remained_pairs)
                    if filtered_out_pairs:
                        logger.info(f"Pair names filtered out by 'exclude' filter: {', '.join(filtered_out_pairs)}")
                    strategy_instance.pair_names = remained_pairs
                    logger.debug(f"Remained pairs names after apply 'exclude' filter on {name}': {len(strategy_instance.pair_names)}")
    
    @classmethod 
    def check_pair_ohlcv_availability(cls):
        cls.active_strategy_pairs.clear()
        logger.debug("Checking OHLCV data availability for all strategies. ")
        for name, strategy_instance in cls._instances.items():
            if hasattr(strategy_instance, 'pair_names') and strategy_instance.pair_names:
                missing_pairs = [x for x in strategy_instance.pair_names if x not in cls.pairs_with_ohlcv_data]
                if missing_pairs:
                    logger.info(f"Filtered out {', '.join(missing_pairs)} from {strategy_instance.name} strategy because no OHLCV data was found for them.")
                    strategy_instance.pair_names = [p for p in strategy_instance.pair_names if p not in missing_pairs]

                cls.active_strategy_pairs.update(strategy_instance.pair_names)
        logger.debug(f"Remained pairs with OHLCV data across all strategies: {len(cls.active_strategy_pairs)}")
 
    def create_pairs(self):
        logger.debug(f"Creating {len(self.pair_names)} Pair objects for strategy '{self.name}'.")
        self.pairs = {p: Pair(p,self.data_provider) for p in self.pair_names}

    def handle_param_table(self):
        def build_default_param_table():
            # Ha nincsenek indikátorok vagy párok, üres táblát adunk vissza a megfelelő oszlopokkal.
            if not self.indicator_names or not self.pair_names:
                return pd.DataFrame(columns=["pair", "indicator"])

            # Keresztkombináció generálása
            combinations = pd.DataFrame(itertools_product(self.pair_names, self.indicator_names), columns=["pair", "indicator"])

            # Alapértelmezett paraméterek betöltése minden indikátorhoz
            default_params = {ind: get_default_params(ind) or {} for ind in self.indicator_names}

            # Alapértelmezett paraméterek DataFrame-é alakítása
            defaults_df = pd.DataFrame.from_records(
                [default_params[ind] for ind in combinations["indicator"]],
            index=combinations.index
            )
            # Kombináció és alapértelmezett paraméterek összefűzése
            default_param_table = pd.concat([combinations, defaults_df], axis=1)
            default_param_table = default_param_table.sort_values(by=['pair', 'indicator']).reset_index(drop=True)
            logger.debug(f"Default parameter table for '{self.name} strategy created.")
            return default_param_table

        def check_table_alignment(cached_param_table, default_param_table):
            # A paraméter táblának azt a kivonatát készítjük el amelyek a stratégiában szereplő párokra vonatkoznak
            cached_param_table = cached_param_table[cached_param_table['pair'].isin(self.pair_names)]
            cached_param_table = cached_param_table.sort_values(by=['pair', 'indicator']).reset_index(drop=True)

            is_same = False
            if cached_param_table[['pair', 'indicator']].equals(default_param_table[['pair', 'indicator']]):
                if set(cached_param_table.columns) == set(default_param_table.columns):
                    if cached_param_table.notnull().equals(default_param_table.notnull()):
                        is_same = True
                        
            if is_same:
                logger.debug(f"Cached parameter table for strategy '{self.name}' is aligned.")
            else:
                logger.info(f"Cached parameter table for strategy '{self.name}' is not aligned. Building new one from default parameters.")
            return is_same        
        
        cached_param_table = self.data_provider.load_strategy_param_table(f"{self.name}.parquet")
        if cached_param_table is not None:
            logger.debug(f"Loaded parameter table for strategy '{self.name}' from {self.name}.parquet")
            # 1. Alapértelmezett paramétereket tartalmazó tábla létrehozása
            default_param_table = build_default_param_table()
            # 3. összehasonlítás
            if check_table_alignment(cached_param_table, default_param_table):
                logger.debug(f"No discrepancy in the loaded parameter table for strategy '{self.name}'.")
                return cached_param_table
            
            else:
                logger.warning(f"Discrepancy found between cached and default parameter tables for strategy '{self.name}'. Merging.")
                cached_param_table = cached_param_table.set_index(['pair', 'indicator'])
                default_param_table = default_param_table.set_index(['pair', 'indicator'])

                updated_param_table = cached_param_table.combine_first(default_param_table).reset_index()
                
                param_names = []
                for ind in self.indicator_names:
                    defaults = get_default_params(ind)
                    param_names.extend(defaults.keys())
                
                columns_order = ['pair', 'indicator'] + param_names
                
                updated_param_table = updated_param_table.reindex(columns=columns_order)
                updated_param_table = updated_param_table.sort_values(by=['pair', 'indicator']).reset_index(drop=True)

                logger.debug(f"Saving merged parameter table for strategy '{self.name}'.")
                self.data_provider.save_strategy_param_table(f'{self.name}.parquet',updated_param_table)
                return updated_param_table

        else:
            logger.info(f"No parameter table found for strategy '{self.name}'. Creating and returning default table.")
            default_param_table = build_default_param_table()
            self.data_provider.save_strategy_param_table(f'{self.name}.parquet',default_param_table)      
            return default_param_table


    def run_all_indicators(self):
        # A stratégián belül, az egyes indikátorokhoz rendelt párok értékeinek kiszámítása
        # Stratégiához tartalmazó paraméter tábla betöltése (ebben szerepelnek a stratégián belül párokhoz tartozó optimalizált indikátor paraméterek)
        logger.debug(f"Running all indicators for strategy '{self.name}'.")
        
        # Ha a stratégiának nincsenek indikátorai, nincs mit futtatni, de létre kell hozni az üres eredmény DF-eket.
        if not self.indicator_names:
            logger.info(f"Strategy '{self.name}' has no indicators, skipping calculation.")
            # Hozzunk létre egy indexet az ohlcv adatokból, hogy a run_logic működjön
            sample_pair = self.pair_names[0] if self.pair_names else None
            logger.debug(f"For strategy {self.name}, the sample_pair is: {sample_pair}")
            if sample_pair:
                index_template = self.data_provider.get_ohlcv(sample_pair, end=self.latest_closed_bar_open_time).index
                for _, pair in self.pairs.items():
                    pair.indicator_results = pd.DataFrame(index=index_template)
            return

        self.param_df = self.handle_param_table()
        for _, pair in self.pairs.items():
            logger.debug(f"Calculating indicators for pair '{pair.symbol}' in strategy '{self.name}'.")
            pair_results =  []
            pair_rows = self.param_df[self.param_df["pair"] == pair.symbol] # A paraméter táblában az aktuális párra vonatkozó sorokat hozzárendelem egy változóhoz
            for _, row in pair_rows.iterrows():
                indicator_name = row["indicator"]
                if indicator_name not in self.indicators:
                    logger.warning(f"Indicator '{indicator_name}' from param table not found in strategy '{self.name}'. Skipping.")
                    continue

                # paraméter táblában lévő indikátor név alapján kiválasztom az indikátor példányt
                indicator = self.indicators[indicator_name]

                # majd kiolvasom az indikátor paramétereit a paraméter táblából
                params = {}

                for k in row.index:
                    # Csak azokat az oszlopokat vesszük figyelembe, amelyek nem 'pair' vagy 'indicator'
                    # és nem NaN értékűek
                    if k not in ["pair", "indicator"] and pd.notnull(row[k]):
                        value = row[k]

                        # Ha az érték szám és egésznek tekinthető → konvertáljuk int-té
                        if isinstance(value, (int, float)) and value == int(value):
                            value = int(value)

                        # Hozzáadjuk a paraméter szótárhoz
                        params[k] = value

                logger.debug(f"Calculte indicator values for '{indicator_name}' on '{pair.symbol}' with params: {params}")
                # kiszámolom a párhoz tartozó aktuális indikátor eredményt
                result = self.calculate_indicator_values(indicator, params, pair.symbol, self.latest_closed_bar_open_time)
                pair_results.append(result)

            
            pair.indicator_results = pd.concat(pair_results, axis=1,)
        logger.info(f"All indicators calculated for '{self.name} strategy.'")
 
    def run_logic(self):
        """
        Kiértékeli a signal_logic szerinti 'buy' és 'sell' feltételeket.
        Kiszámol egy df-et, amelynek indexe 'open_time',
        """
        logger.debug(f"Running signal logic for strategy '{self.name}'.")
        # -- Cache betöltés és számítási tartomány meghatározása --
        old_signals_file = f"pairs_for_buy_{self.name}.parquet"
        old_file, old_signals, cache_end_point = self.data_provider.load_and_check_old_cached_data('signals', old_signals_file)
    
        pairs_for_buy_dict = {}
        for _, pair in self.pairs.items():
            logger.debug(f"Evaluating logic for pair '{pair.symbol}' in strategy '{self.name}'.")
            df = pair.indicator_results
            # Ha az indikátorok futtatása nem hozott létre eredményt (pl. NoTrade),
            # de van indexünk, akkor azzal dolgozunk tovább.
            if df.empty and not df.index.empty:
                pass # Mehet tovább, az index rendben van.

            # ---- Buy/Sell jelzések ----
            signal = pd.DataFrame(index=df.index)
            
            for sig_type in ['buy', 'sell']:
                logic = self.signal_logic[sig_type]
                logger.debug(f"Evaluating '{sig_type}' logic for '{pair.symbol}': {logic}")
                if isinstance(logic, bool):
                    # Ha a logika egy egyszerű bool (pl. False), akkor ne használjunk eval-t.
                    signal[sig_type] = pd.Series(logic, index=df.index, dtype=bool)
                else:
                    signal[sig_type] = df.eval(logic)

            # ---- Események felépítése ----
            # prioritás: SELL > BUY
            event = pd.Series(
                np.where(signal['sell'], -1,
                        np.where(signal['buy'], 1, np.nan)),
                index=signal.index
            )

            # Utolsó esemény továbbvitele (ffill), kezdésnél SELL-nek (-1) tekintjük
            last_event = event.ffill().fillna(-1).astype(int)

            # Alap: minden OUT
            state = pd.Series("OUT", index=signal.index)
            
            # HOLD: ha nincs új event, de az utolsó BUY
            mask_hold = event.isna() & (last_event == 1)


            state[mask_hold] = "HOLD"

            # BUY és SELL események felülírják
            state[event == 1] = "BUY"
            state[event == -1] = "SELL"
              

            """
                | idő | buy | sell | event | last_event | state |
                |-----|-----|------|-------|------------|--------|
                | 1   | F   | T    | -1    | -1         | SELL   |
                | 2   | F   | F    | NaN   | -1         | OUT    |
                | 3   | T   | F    | 1     | 1          | BUY    |
                | 4   | F   | F    | NaN   | 1          | HOLD   |
                | 5   | F   | F    | NaN   | 1          | HOLD   |
                | 6   | F   | T    | -1    | -1         | SELL   |
                | 7   | F   | F    | NaN   | -1         | OUT    |

                Állapotlogika (state):

                A stratégia két logikai feltételt értékel: "buy" és "sell".
                Ezek eredménye alapján egy "event" sorozat épül fel:
                    - 1 → új BUY esemény
                    - -1 → új SELL esemény
                    - NaN → nincs új esemény

                Az "event" sorozatból képzett "last_event" oszlop az utolsó ismert eseményt viszi tovább (ffill).
                Az állapot (state) ezek alapján kerül meghatározásra:

                - új BUY esemény esetén           → "BUY"
                - BUY után, új esemény hiányában  → "HOLD"
                - új SELL esemény esetén          → "SELL"
                - SELL után, új esemény hiányában → "OUT"

                Megjegyzés:
                - "BUY" és "SELL" stratégiai események, a feltételek explicit teljesüléséből erednek.
                - "HOLD" és "OUT" származtatott állapotok, kizárólag a korábbi eseményekből következnek.
                - A prioritás: SELL > BUY → ha mindkettő teljesülne, a SELL felülírja a BUY-t.
            """


            # in_position = akkor True, ha az utolsó esemény BUY volt
            signal['state'] = state

            # ---- Eredmények eltárolása és CSV kiírás ----

            pairs_for_buy_dict[pair.symbol] = signal['state']

        
        # Összeolvasztja párokra meghatározott sratégia eredményeket, ha vannak
        if pairs_for_buy_dict:
            pairs_for_buy = pd.concat(pairs_for_buy_dict, axis=1)
        
        
        # Ha üres szótár lenne (mert nem volt pár a stratégiában), akkor létrehoz egy üres szótárat index-el
        else:
            logger.debug(f"No pairs to process for strategy '{self.name}', creating empty DataFrame.")
            pairs_for_buy = pd.DataFrame()
            index = pd.date_range(start=pd.to_datetime("2025-01-01 00:00:00").tz_localize("UTC"), end=self.latest_closed_bar_open_time, freq="4h", tz="UTC")
            pairs_for_buy.index = index
            pairs_for_buy.index.name = "open_time"

        # Ha van cache fájl, akkor a régi adatok tiszteletben tartásával, hozzáadja az új értékeket. 
        # Ha az új értékekben nincsenek párok, akkor Null értékkel fűzi hozzá a régi adatokhoz
        if old_file:
            logger.debug(f"Merging new signals with cached signals for strategy '{self.name}'.")
            new_data_slice = pairs_for_buy[pairs_for_buy.index > cache_end_point]
            pairs_for_buy = old_signals.combine_first(new_data_slice)

        logger.debug(f"Saving signal states for strategy '{self.name}' to '{old_signals_file}'.")
        self.data_provider.save_cache('signals',old_signals_file, pairs_for_buy)
        self.pairs_for_buy = pairs_for_buy

    def calculate_indicator_values(self,indicator_inst, params, symbol, operation_end):
        # -- Cache betöltés és számítási tartomány meghatározása --
        filename = f"{indicator_inst.name}_{symbol}_{generate_params_hash(params)}.parquet".replace("/", "-")
        
        cache, loaded_cache, cache_end_point = self.data_provider.load_and_check_old_cached_data('cache',filename)
        logger.debug(f"Calculating indicator values for {indicator_inst.name} - {symbol}. cache: {cache}, cache_end_point: {cache_end_point}")
        operation_start = cache_end_point
        operation_end = operation_end
        logger.debug(f"operation_start: {operation_start}, operation_end: {operation_end}")



        # Ha van cache fájl...
        if cache:
            logger.debug("Cache exists.")
            # Ha van cache fájl és van hiányzó számítás:
            if operation_start < operation_end:
                logger.debug("New indicator value calculation is required.")
                # Új, régi adakon túli új adatok kiszámítása és verifikálása önmagához

                def calc_and_verify_new_values(warmup=200, multiplier=500, max_iter=5, bar_delta=self.timeframe_delta):
                    prev_iter_results = None
                    is_stable = False

                    for i in range(max_iter):
                        # Számítási időszak meghatározása
                        bars = warmup + i * multiplier
                        calculate_start = operation_start - bars * bar_delta

                        # OHLCV adatok betöltése
                        ohlcv = indicator_inst.data_provider.get_ohlcv(symbol, start=calculate_start, end=operation_end)

                        # Indikátor újraszámolása
                        recalced_values = indicator_inst.func(ohlcv, **params)

                        # Csak az adott időszakot vizsgáljuk
                        index_slice = slice(operation_start, operation_end)
                        recalced_values_slice = recalced_values.loc[index_slice].round(5)

                        # Stabilizáció ellenőrzése
                        if prev_iter_results is not None and recalced_values_slice.equals(prev_iter_results) and not is_all_float_zero(recalced_values_slice):
                            logger.info(f"Calculating {indicator_inst.name} indicator for {symbol}. Calculation stabilized after {i}. iteration(s).")
                            is_stable = True
                            break

                        # Elmentjük az aktuális iteráció eredményét
                        prev_iter_results = recalced_values_slice

                    if not is_stable:
                        logger.warning(f"Indicator {indicator_inst.name}-{symbol} did not stabilize within maximum iterations.")
                        # OHLCV adatok betöltése a teljes tartományra
                        ohlcv = indicator_inst.data_provider.get_ohlcv(symbol, end=operation_end)
                        # Indikátor újraszámolása
                        recalced_values = indicator_inst.func(ohlcv, **params)

                    return (is_stable, recalced_values, operation_start)
                
                is_stable, recalced_values, operation_start = calc_and_verify_new_values()
                calculated_values = loaded_cache.combine_first(recalced_values)
                self.data_provider.save_cache('cache',filename,calculated_values)
                return calculated_values
                
                
            else:
                # Ha van cache fájl és nincs hiányzó számítás:
                return loaded_cache
    
        else:
            logger.warning(f"Cache does not exist for {indicator_inst.name} - {symbol}. Calculating.")
            ohlcv = indicator_inst.data_provider.get_ohlcv(symbol, end=operation_end)
            calculated_values = indicator_inst.func(ohlcv, **params)
            self.data_provider.save_cache('cache',filename,calculated_values)
            return calculated_values
            
    
    @classmethod
    def merge_strategies(cls, data_provider):
        """
        Egyesíti az összes stratégia `pairs_for_buy` DataFrame-jét és elmenti.
        """
        logger.debug("Merging results from all strategies.")
        all_dfs = {}
        for name, strategy_instance in cls._instances.items():
            if hasattr(strategy_instance, 'pairs_for_buy') and strategy_instance.pairs_for_buy is not None:
                # Multi-index létrehozása a stratégia nevével
                df = strategy_instance.pairs_for_buy.copy()
                all_dfs[name] = df
                
        logger.debug(f"Found {len(all_dfs)} strategies to merge: {list(all_dfs.keys())}")

        if not all_dfs:
            logger.info("No `pairs_for_buy` datasets to merge.")
            return

        # A stratégiák eredményeinek összefésülése a combine_first segítségével.
        # Ez kitölti a hiányzó (NaN) értékeket a következő stratégiából.
        final_combined = reduce(lambda left, right: left.combine_first(right), all_dfs.values())

        cls.final_combined = final_combined
        data_provider.save_cache('signals', 'pairs_for_buy.parquet', final_combined)
        logger.info("Merged strategy results saved: pairs_for_buy.parquet")
        
    @classmethod
    def apply_entry_filters(cls, data_provider, efilter, latest_closed_bar_open_time):
        """
        Belépési szűrők (entry filters) alkalmazása az egyesített 'BUY' jelzésekre.
        A szűrők hatására bizonyos 'BUY' állapotok 'SELL'-é alakulnak, ha a megadott feltételek nem teljesülnek.
        
        A korábban már kiszámolt és fájlba mentett eredményeket betölti (ha vannak), 
        és csak az új gyertyákra futtatja a szűrést. A régi értékeket újrahasznosítja.
        Az eredmény a `cls.final_combined` táblában frissül, és a cache fájlba is elmentésre kerül.
        
        Paraméterek:
        -----------
        efilter : dict
            Szűrőfeltételeket tartalmazó szótár, pl.:
            {
                'volume': 50_000_000,  # USD értékben
                ...
            }
        """
  
        def use_mask(input_df,filter_df):
            """
            BUY jelzések szűrése maszkolás alapján.
            Azon pontokon, ahol 'BUY' van, de a filter False → átírjuk 'SELL'-re.

            Paraméterek:
            -----------
            input_df : pd.DataFrame
                A `pairs_for_buy` tábla adott szelete (időintervallum).
            filter_df : pd.DataFrame (bool)
                Igaz/hamis maszkoló tábla, azonos index-szel és oszlopokkal.

            Visszatér:
            ---------
            pd.DataFrame : Szűrt eredmény.
            """
            
            filter_df = filter_df.fillna(False)
            mask = (input_df == "BUY") & (~filter_df)
            # A `mask` DataFrame-et bool típussá kell konvertálni a `where` előtt.
            filtered_df = input_df.where(~mask.astype(bool), "SELL")
            return filtered_df
       
        # -- Cache betöltés és számítási tartomány meghatározása --
        old_signals_file = "pairs_for_buy_filtered.parquet"
        old_file, old_signals, cache_end_point = data_provider.load_and_check_old_cached_data('signals', old_signals_file)

        if not hasattr(cls, 'final_combined') or cls.final_combined.empty:
            logger.info("No merged strategy result (`final_combined`), skipping filtering.")
            return

        operation_start = cache_end_point if old_file else cls.final_combined.index[0]
        operation_end = latest_closed_bar_open_time
        logger.debug(f" operation_start: {operation_start} operation_end: {operation_end}")

        # -- Ellenőrzés, hogy van-e egyáltalán 'BUY' jelzés a teljes adathalmazban --
        if not (cls.final_combined == 'BUY').any().any():
            logger.info("No filterable 'BUY' signals in merged strategies, skipping filtering and saving.")
            cls.final_combined = old_signals if old_file else cls.final_combined
            data_provider.save_cache('signals', old_signals_file, cls.final_combined)
            return

        #  -- Szűrendő tartomány kivágása --
        raw_signals = cls.final_combined[(cls.final_combined.index >= operation_start) & (cls.final_combined.index <= operation_end)]
        
        # -- Szűrők alkalmazása --
        for filter_name, rule in efilter.items():
            logger.info(f"Applying '{filter_name}' filter with rule: {rule}")
            if filter_name == 'volume':
                series_dict = {}
                for pair in cls.active_strategy_pairs:
                    pair_data = data_provider.get_ohlcv(pair,start=operation_start, end=operation_end, columns=['close','volume'])
                    pair_series = pair_data['close'] * pair_data['volume']
                    series_dict[pair] = pair_series

                volume_df = pd.concat(series_dict, axis=1)
                volume_filter = volume_df > rule
                data_provider.save_cache('signals','volume_filter.parquet', volume_filter)
                raw_signals = use_mask(raw_signals ,volume_filter)

            elif filter_name == 'fgi':
                fgi_filename = f"fear_greed_index_{'4h'}.parquet"
                fgi = data_provider.load_cache('fgi',fgi_filename, start=operation_start,end=operation_end)
                fgi_series = fgi['fear_greed_value']
                fgi_series = fgi_series.reindex(raw_signals.index).fillna(100)
                fgi_filter = pd.DataFrame({symbol: fgi_series for symbol in raw_signals.columns}, index=raw_signals.index) >= rule
                data_provider.save_cache('signals','fgi_filter.parquet', fgi_filter)
                raw_signals = use_mask(raw_signals, fgi_filter)

            elif filter_name == 'require_previous_lower':
                series_dict = {}
                for pair in cls.active_strategy_pairs:
                    pair_instance = Pair.get_pair_by_attribute('symbol', pair)
                    series_dict[pair] = pair_instance.indicator_results['wt_entry_greater']

                entry_greater_df = pd.concat(series_dict, axis=1)
                entry_greater_filter = entry_greater_df == rule
                data_provider.save_cache('signals','entry_greater_filter.parquet', entry_greater_filter)
                raw_signals = use_mask(raw_signals, entry_greater_filter)


            elif filter_name == 'adx':
                series_dict = {}
                for pair in cls.active_strategy_pairs:
                    pair_instance = Pair.get_pair_by_attribute('symbol', pair)
                    series_dict[pair] = pair_instance.indicator_results['adx']

                adx_df = pd.concat(series_dict, axis=1)
                adx_filter = adx_df >= rule
                data_provider.save_cache('signals','adx_filter.parquet', adx_filter)
                raw_signals = use_mask(raw_signals, adx_filter)



        # -- Új és régi adatok összefűzése, mentés és frissítés --
        filtered_signals = old_signals.combine_first(raw_signals) if old_file else raw_signals
        data_provider.save_cache('signals',old_signals_file, filtered_signals)
        cls.final_combined = filtered_signals

    @classmethod
    def apply_sorting(cls, data_provider, sorting_method, latest_closed_bar_open_time):
       # -- Cache betöltés és számítási tartomány meghatározása --
        old_signals_file = "pairs_for_buy_ranked.parquet"
        old_file, old_signals, cache_end_point = data_provider.load_and_check_old_cached_data('signals', old_signals_file)
        
        if not hasattr(cls, 'final_combined') or cls.final_combined.empty:
            logger.info("Nothing to rank, `final_combined` is empty.")
            return

        operation_start = cache_end_point if old_file else cls.final_combined.index[0]
        operation_end = latest_closed_bar_open_time

        #  -- Sorbarendezéhez tartomány kivágása --
        raw_signals = cls.final_combined[(cls.final_combined.index >= operation_start) & (cls.final_combined.index <= operation_end)]
        
        logger.info(f"Applying sorting method: '{sorting_method}'.")
        # -- Sorbarendezés alkalmazása --
        if sorting_method == 'volume':
            # Volume-alapú sorbarendezés: close * volume minden párhoz, adott időintervallumban
            sort_dict = {}
            for pair_symbol in cls.active_strategy_pairs:
                pair_data = data_provider.get_ohlcv(pair_symbol, start=operation_start, end=operation_end, columns=['close','volume'])
                pair_series = pair_data['close'] * pair_data['volume']
                sort_dict[pair_symbol] = pair_series

            sort_df = pd.concat(sort_dict, axis=1)

        elif sorting_method == 'adx':
            # ADX-alapú sorbarendezés
            sort_dict = {}
            for pair_symbol in cls.active_strategy_pairs:
                pair_instance = Pair.get_pair_by_attribute('symbol', pair_symbol)
                if pair_instance and 'adx' in pair_instance.indicator_results.columns:
                    sort_dict[pair_symbol] = pair_instance.indicator_results['adx']

            sort_df = pd.concat(sort_dict, axis=1)

        else:
            logger.warning(f"Unknown sorting method: {sorting_method}. Skipping ranking.")
            return

        # Maszk: csak BUY cellák legyenek, többi NaN
        buy_mask = raw_signals == "BUY"
        sort_df = sort_df.where(buy_mask)

        # Rangszámítás a BUY-os párok között (csökkenő sorrendben)
        ranks = sort_df.rank(axis=1, method="first", ascending=False)
  
        # -- Új és régi adatok összefűzése --
        if old_file:
            new_sorted_signals = ranks
            sorted_signals = old_signals.combine_first(new_sorted_signals)
        else:
            sorted_signals = ranks
        
       # -- Mentés és frissítés --
        data_provider.save_cache('signals', old_signals_file, sorted_signals)
        logger.debug(f"Ranked BUY signals saved: {old_signals_file}")
    
