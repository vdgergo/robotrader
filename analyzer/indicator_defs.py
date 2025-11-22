import pandas as pd
import numpy as np
import datetime
import talib
from scipy.stats import linregress
from scipy.signal import argrelextrema

import re

import os

# def wavetrend(df, n1=8, n2=12, factor=0.015, smooth_window=4):
#     """
#     WaveTrend indikátor kiszámítása rugalmas paraméterekkel.

#     :param hlc3_data: DataFrame (tartalmazza: high, low, close)
#     :param n1: Channel Length (alapértelmezett: 10)
#     :param n2: Average Length (alapértelmezett: 21)
#     :param factor: Osztó konstans (alapértelmezett: 0.015)
#     :param smooth_window: Rolling átlag a wt_2 simításához (alap: 4)
#     :return: DataFrame a WaveTrend indikátor értékeivel
#     """
#     # Idő index ellenőrzése, megkövetelése
#     if df.index.tz is None or str(df.index.tz) != 'UTC':
#         raise ValueError("A DataFrame indexének UTC időzónásnak kell lennie.")
    
#     # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
#     df = df.copy()
#     df.columns = [col.lower() for col in df.columns]

#     if len(df) < n2:
#         emptydf = pd.DataFrame(columns=['wt_1', 'wt_2', 'wavetrend_signal', 'wt_entry', 'wt_exit', 'wt_diff', 'wt_diff_derivative', 'wt_signal_gap', 'wt_buy_gap', 'wt_buy_pct', 'wt_vctr_l', 'wt_vctr_s'])  # Üres DataFrame 
#         emptydf.index = df.index
#         return emptydf
    
#     # HLC3 kiszámítása
#     hlc3 = (df['high'] + df['low'] + df['close']) / 3
    
#     # WaveTrend indikátor számítása
#     esa = hlc3.ewm(span=n1, adjust=False).mean()
#     di = abs(hlc3 - esa).ewm(span=n1, adjust=False).mean()
#     ci = (hlc3 - esa) / (factor * di)  # Ezen a ponton a factor befolyásolja az érzékenységet
#     tci = ci.ewm(span=n2, adjust=False).mean()
    
#     # WT1 és WT2 kiszámítása
#     wt_1 = tci
#     wt_2 = wt_1.rolling(window=smooth_window).mean()  # Sima átlag a jelgörbére
    
#     # Adatok DataFrame-ben
#     wtframe = pd.DataFrame({
#         "wt_1": wt_1,
#         "wt_2": wt_2,
#     })

#     wtframe.index = pd.to_datetime(wtframe.index, utc=True)

#     # Jelzések számítása
#     wtframe['wavetrend_signal'] = wtframe['wt_1'] > wtframe['wt_2']
#     wtframe['wt_entry'] = (wtframe['wt_1'].shift(1) < wtframe['wt_2'].shift(1)) & (wtframe['wt_1'] > wtframe['wt_2'])
#     wtframe['wt_exit'] = (wtframe['wt_1'].shift(1) > wtframe['wt_2'].shift(1)) & (wtframe['wt_1'] < wtframe['wt_2'])
    
#     # Trend gyorsulását kifejező paraméterek
#     wtframe["wt_diff"] = wtframe["wt_1"] - wtframe["wt_2"]
#     wtframe["wt_diff_derivative"] = wtframe["wt_diff"].diff()

#     # Lokális minimumok indexei a close alapján
#     min_idxs = argrelextrema(df["close"].values, np.less, order=1)[0]
#     # datetime + integer pozíció mentés
#     min_times = wtframe.index[min_idxs]
#     min_pos_dict = {t: i for t, i in zip(min_times, min_idxs)}
#     # Eredmény oszlop
#     wtframe["wt_signal_gap"] = np.nan
#     # Végigmegyünk a belépési pontokon
#     for entry_time in wtframe.index[wtframe["wt_entry"]]:
#         entry_pos = wtframe.index.get_loc(entry_time)
        
#         # Olyan min idők, amik előtte voltak
#         past_mins = [t for t in min_times if t < entry_time]
        
#         if not past_mins:
#             continue

#         last_min_time = past_mins[-1]
#         last_min_pos = min_pos_dict[last_min_time]
#         bar_distance = entry_pos - last_min_pos

#         wtframe.at[entry_time, "wt_signal_gap"] = bar_distance
    

#     # --- Blokk-analízis: csak ahol wt_signal == True ---
#     wtframe[['wt_buy_gap', 'wt_buy_pct', 'wt_vctr_l', 'wt_vctr_s']] = np.nan
#     wtframe['wt_signal_group'] = (wtframe['wavetrend_signal'] != wtframe['wavetrend_signal'].shift()).cumsum()

#     for _, block in wtframe[wtframe['wavetrend_signal']].groupby('wt_signal_group'):
#         entry_rows = block[block['wt_entry']]
#         if entry_rows.empty:
#             continue
#         entry_time = entry_rows.index[0]
#         entry_price = df.loc[entry_time, 'close']

#         # Előző minimum idejét kivesszük az első sorból, és továbbadjuk a többi sornak
#         gap_val = wtframe.at[entry_time, 'wt_signal_gap'] if 'wt_signal_gap' in wtframe.columns else np.nan
#         for i, ts in enumerate(block.index):
#             if ts < entry_time:
#                 continue
#             cp = df.loc[ts, 'close']
#             price_diff_pct = (cp - entry_price) / entry_price * 100
#             bars = i
#             vector_len = np.sqrt(10*bars**2 + (cp - entry_price)**2)
#             slope = ((cp - entry_price) / bars) * 100 if bars != 0 else 0

#             wtframe.at[ts, 'wt_buy_gap'] = bars
#             wtframe.at[ts, 'wt_buy_pct'] = price_diff_pct
#             wtframe.at[ts, 'wt_vctr_l'] = vector_len
#             wtframe.at[ts, 'wt_vctr_s'] = slope

#             if ts != entry_time:
#                 wtframe.at[ts, "wt_signal_gap"] = gap_val
#     wtframe = wtframe.drop(columns=['wt_exit','wt_signal_group'])

#     # Új oszlop: wt_entry_greater
#     wtframe['wt_entry_greater'] = False
#     prev_price = None
#     for ts in wtframe.index:
#         if wtframe.at[ts, 'wt_entry']:
#             price = df.at[ts, 'close']
#             wtframe.at[ts, 'wt_entry_greater'] = (prev_price is not None) and (price > prev_price)
#             prev_price = price

#     print(wtframe.columns)    
#     return wtframe



#  import pandas as pd

def wavetrend(df, n1=8, n2=12, factor=0.015, smooth_window=4):
    """
    WaveTrend indikátor számítása, csak az alábbi oszlopokkal:
    ['wt_1', 'wt_2', 'wavetrend_signal', 'wt_entry', 'wt_entry_greater']
    """

    # Idő index ellenőrzése
    if df.index.tz is None or str(df.index.tz) != 'UTC':
        raise ValueError("A DataFrame indexének UTC időzónásnak kell lennie.")
    
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    if len(df) < n2:
        emptydf = pd.DataFrame(
            columns=['wt_1', 'wt_2', 'wavetrend_signal', 'wt_entry', 'wt_entry_greater'],
            index=df.index
        )
        return emptydf

    # HLC3 kiszámítása
    hlc3 = (df['high'] + df['low'] + df['close']) / 3

    # WaveTrend alap számítás
    esa = hlc3.ewm(span=n1, adjust=False).mean()
    di = abs(hlc3 - esa).ewm(span=n1, adjust=False).mean()
    ci = (hlc3 - esa) / (factor * di)
    tci = ci.ewm(span=n2, adjust=False).mean()

    wt_1 = tci
    wt_2 = wt_1.rolling(window=smooth_window).mean()

    wtframe = pd.DataFrame({
        "wt_1": wt_1,
        "wt_2": wt_2,
    }, index=pd.to_datetime(df.index, utc=True))

    # Jelzések
    wtframe['wavetrend_signal'] = wtframe['wt_1'] > wtframe['wt_2']
    wtframe['wt_entry'] = (wtframe['wt_1'].shift(1) < wtframe['wt_2'].shift(1)) & \
                          (wtframe['wt_1'] > wtframe['wt_2'])

    # Új oszlop: wt_age
    wtframe['wt_age'] = 0
    age = 0
    for ts in wtframe.index:
        if wtframe.at[ts, 'wt_1'] > wtframe.at[ts, 'wt_2']:
            age += 1
            wtframe.at[ts, 'wt_age'] = age
        else:
            age = 0
            wtframe.at[ts, 'wt_age'] = 0
    
    # Új oszlop: wt_entry_greater
    wtframe['wt_entry_greater'] = False
    prev_price = None
    for ts in wtframe.index:
        if wtframe.at[ts, 'wt_entry']:
            price = df.at[ts, 'close']
            wtframe.at[ts, 'wt_entry_greater'] = (prev_price is not None) and (price > prev_price)
            prev_price = price

    return wtframe[['wt_1', 'wt_2', 'wavetrend_signal', 'wt_entry', 'wt_entry_greater', 'wt_age']]
     
def nwr(df, src_col='close', h=8.0, r=8.0, x_0=25, lag=2, kernel_type='rational_quadratic'):
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]
    src = df[src_col].values
    n = len(src)
    yhat1 = np.full(n, np.nan)
    yhat2 = np.full(n, np.nan)

    def get_weight(i, h, r, kernel):
        if kernel == 'rational_quadratic':
            return (1 + (i**2) / (2 * r * h**2))**(-r)
        elif kernel == 'gaussian':
            return np.exp(-0.5 * (i / h)**2)
        elif kernel == 'epanechnikov':
            return max(0, 1 - (i / h)**2)
        else:
            raise ValueError("Ismeretlen kernel: " + kernel)

    for t in range(x_0, n):
        current_weight = 0.0
        cumulative_weight = 0.0
        for i in range(t - x_0 + 1):  # 0..(t-x_0)
            y = src[t - i]
            w = get_weight(i, h, r, kernel_type)
            current_weight += y * w
            cumulative_weight += w
        if cumulative_weight != 0:
            yhat1[t] = current_weight / cumulative_weight

        # Lag-re simán csak ugyanez, csak `t - lag`
        if t - lag >= x_0:
            current_weight_lag = 0.0
            cumulative_weight_lag = 0.0
            for i in range(t - lag - x_0 + 1):
                y = src[t - lag - i]
                w = get_weight(i, h, r, kernel_type)
                current_weight_lag += y * w
                cumulative_weight_lag += w
            if cumulative_weight_lag != 0:
                yhat2[t] = current_weight_lag / cumulative_weight_lag

    result = pd.DataFrame(index=df.index)
    result['nwr_yhat1'] = yhat1
    result['nwr_yhat2'] = yhat2
    result['nwr_signal'] = result['nwr_yhat1'] > result['nwr_yhat2']
    result['nwr_entry'] = (result['nwr_yhat1'].shift(1) < result['nwr_yhat2'].shift(1)) & (result['nwr_yhat1'] > result['nwr_yhat2'])

    return result

def atr(df,length=14):
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]
    df = df.copy()
    df['ATR'] = talib.ATR(df['high'], df['low'], df['close'], timeperiod=length)
    return df[['ATR']]

def bullbeartrend(df,atr_period=4,window_length=12):  #treshold értékkel a drawdown-ok száma csökkenthető, de csökken a profit is
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    # ATR számítása
    atr_5 = talib.ATR(df['high'], df['low'], df['close'], timeperiod=atr_period)

    # BullTrend és BearTrend számítása
    df['bulltrend'] = (df['close'] - df['low'].rolling(window=window_length).min()) / atr_5
    df['beartrend'] = (df['high'].rolling(window=window_length).max() - df['close']) / atr_5
    df['beartrend2'] = -1 * df['beartrend']

    # BullTrend_hist és BearTrend_hist
    df['beartrend_hist'] = np.where(df['bulltrend'] < 2, df['bulltrend'] - 2, 0)
    df['bulltrend_hist'] = np.where(df['beartrend2'] > -2, df['beartrend2'] + 2, 0)
    
    df["bullbeartrend_signal"] = df['bulltrend_hist'] > 0
    # Az eredményeket tartalmazó oszlopok
    return df[['bulltrend_hist','beartrend2','bullbeartrend_signal']]

def volatility_stop(df, length=20, mult=2):
    """
    Volatility Stop function `iloc`-os verziója, `vstop_signal` és `vstop%` hozzáadásával.
    
    :param df: pandas DataFrame containing 'close' price.
    :param length: ATR period length (default=20).
    :param mult: Multiplier for ATR (default=2).
    :return: pandas DataFrame containing Volatility Stop values.
    """
    
    if df.empty or len(df) < 2:
        return df  # Ha a DataFrame túl kicsi, kilépünk
    df = df.copy()
    # ATR számítása
    df['tr'] = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)

    df['atr'] = df['tr'].rolling(window=length).mean()

    # Kezdőértékek beállítása
    df['max1'] = df['close']  # Kezdeti értékek
    df['min1'] = df['close']
    df['is_uptrend'] = True
    df['vstop'] = df['close']
    df['vstop_signal'] = False  # Új oszlop hozzáadása
    df['vstop_pct'] = 0.0  # Százalékos különbség oszlop inicializálása

    # Iteráció `iloc` segítségével
    for i in range(1, len(df)):
        prev_idx = i - 1  # Előző sor pozíciója

        df.iloc[i, df.columns.get_loc('max1')] = (
            max(df.iloc[prev_idx, df.columns.get_loc('max1')], df.iloc[i, df.columns.get_loc('close')])
            if df.iloc[prev_idx, df.columns.get_loc('is_uptrend')]
            else df.iloc[i, df.columns.get_loc('close')]
        )

        df.iloc[i, df.columns.get_loc('min1')] = (
            min(df.iloc[prev_idx, df.columns.get_loc('min1')], df.iloc[i, df.columns.get_loc('close')])
            if not df.iloc[prev_idx, df.columns.get_loc('is_uptrend')]
            else df.iloc[i, df.columns.get_loc('close')]
        )

        # Stop érték kiszámítása
        stop = (
            df.iloc[prev_idx, df.columns.get_loc('max1')] - mult * df.iloc[i, df.columns.get_loc('atr')]
            if df.iloc[prev_idx, df.columns.get_loc('is_uptrend')]
            else df.iloc[prev_idx, df.columns.get_loc('min1')] + mult * df.iloc[i, df.columns.get_loc('atr')]
        )

        # vstop kiszámítása trend szerint
        df.iloc[i, df.columns.get_loc('vstop')] = (
            max(df.iloc[prev_idx, df.columns.get_loc('vstop')], stop)
            if df.iloc[prev_idx, df.columns.get_loc('is_uptrend')] and df.iloc[i, df.columns.get_loc('close')] >= df.iloc[prev_idx, df.columns.get_loc('vstop')]
            else stop
        )

        # Trend irányának ellenőrzése
        df.iloc[i, df.columns.get_loc('is_uptrend')] = df.iloc[i, df.columns.get_loc('close')] >= df.iloc[i, df.columns.get_loc('vstop')]

        # vstop_signal generálása (pozitív, ha trendfordulás történt)
        df.iloc[i, df.columns.get_loc('vstop_signal')] = (
            df.iloc[i, df.columns.get_loc('is_uptrend')] != df.iloc[prev_idx, df.columns.get_loc('is_uptrend')]
        )

        # vstop% kiszámítása (vstop és close közötti százalékos különbség)
        df.iloc[i, df.columns.get_loc('vstop_pct')] = (
            (df.iloc[i, df.columns.get_loc('vstop')] - df.iloc[i, df.columns.get_loc('close')]) / df.iloc[i, df.columns.get_loc('close')]
        )
    return df[['vstop', 'is_uptrend', 'vstop_signal', 'vstop_pct']]

def adx(df, adx_length=14,adx_threshold=20):
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    df['adx'] = talib.ADX(df['high'], df['low'], df['close'], timeperiod=adx_length)
    df['plusdi'] = talib.PLUS_DI(df['high'], df['low'], df['close'], timeperiod=adx_length)
    df['-di'] = talib.MINUS_DI(df['high'], df['low'], df['close'], timeperiod=adx_length)
    df['adx_signal'] = (df['adx'] >= adx_threshold) & (df['plusdi'] > df['-di'])
    return df[['plusdi','-di','adx', 'adx_signal']]

def bollinger_bands(df, period=20, std_dev=2):
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    df['upper_band'], df['middle_band'], df['lower_band'] = talib.BBANDS(
        df['close'], timeperiod=period, nbdevup=std_dev, nbdevdn=std_dev, matype=0
    )
    df['BBheightPct'] = df['upper_band'] / df['lower_band'] - 1
    df['BBheightPct'] = talib.SMA(df['BBheightPct'],timeperiod=5)
    df['BBheightPrice%'] = (df['close'] - df['lower_band']) / (df['upper_band'] - df['lower_band'])
    
    return df[['upper_band','middle_band','lower_band','BBheightPct','BBheightPrice%']]

def rsi(df, rsi_length=14, sma_length=14):
    """
    Relative Strength Index (RSI), Simple Moving Average (SMA), és SMA dőlésszög számítása.

    RSI = 100 - (100 / (1 + RS)), ahol RS az átlagos emelkedés / átlagos csökkenés
    SMA = RSI mozgóátlaga
    SMA Slope = SMA aktuális értéke - előző értéke

    :param df: pandas DataFrame, amely tartalmazza a 'close' oszlopot
    :param rsi_length: Az RSI számítási periódusa (alapértelmezett: 14)
    :param sma_length: Az SMA számítási periódusa (alapértelmezett: 14)
    :return: pandas DataFrame az RSI, SMA és SMA dőlésszög értékekkel
    """
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    delta = df['close'].diff()
    
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=rsi_length, min_periods=1).mean()
    avg_loss = loss.rolling(window=rsi_length, min_periods=1).mean()

    rs = avg_gain / (avg_loss + 1e-10)  # Nullával való osztás elkerülése
    df['rsi'] = 100 - (100 / (1 + rs))

    df['sma'] = df['rsi'].rolling(window=sma_length, min_periods=1).mean()

    df['sma_slope'] = df['sma'].diff()  # SMA dőlésszög számítása

    df['rsi_signal'] = df['rsi'] >= 65  # Egyszerű jel: ár az EMA fölött

    return df[['rsi', 'sma', 'sma_slope','rsi_signal']]

def roc(df, length=9):
    """
    Rate of Change (ROC) indikátor számítása.
    ROC = 100 * (záróár - záróár N periódussal ezelőtt) / záróár N periódussal ezelőtt
    """
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    df['ROC'] = 100 * (df['close'] - df['close'].shift(length)) / df['close'].shift(length)
    
    return df[['ROC']]

def pso(df, stochlen=8, smoothlen=25):
    """
    Premier Stochastic Oscillator (PSO) számítása.
    """
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    # Stochastic %K kiszámítása
    lowest_low = df['low'].rolling(stochlen).min()
    highest_high = df['high'].rolling(stochlen).max()
    df['%K'] = 100 * (df['close'] - lowest_low) / (highest_high - lowest_low)

    # Az nsk kiszámítása
    df['nsk'] = 0.1 * (df['%K'] - 50)

    # A sqrt(smoothlen) érték kiszámítása és kerekítése
    len_smooth = int(np.sqrt(smoothlen))

    # Kétszeres EMA (exponenciális mozgóátlag)
    df['ss'] = talib.EMA(talib.EMA(df['nsk'], timeperiod=len_smooth), timeperiod=len_smooth)

    # Exponenciális normalizálás
    df['expss'] = np.exp(df['ss'])
    df['PSO'] = (df['expss'] - 1) / (df['expss'] + 1)
    
    return df[['PSO']]

def vfi(df, length=130, coef=0.2, vcoef=2.5, signal_length=5):
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    
    # Typical Price
    df['typical'] = (df['high'] + df['low'] + df['close']) / 3
    
    # Log differencia és volatilitás
    df['inter'] = np.log(df['typical']) - np.log(df['typical'].shift(1))
    df['vinter'] = df['inter'].rolling(30).std()
    
    # Cutoff érték kiszámítása
    df['cutoff'] = coef * df['vinter'] * df['close']
    
    # Volumen korlátozása
    df['vave'] = df['volume'].rolling(length).mean().shift(1)
    df['vmax'] = df['vave'] * vcoef
    df['vc'] = np.where(df['volume'] < df['vmax'], df['volume'], df['vmax'])
    
    # Money Flow számítása
    df['mf'] = df['typical'] - df['typical'].shift(1)
    df['vcp'] = np.where(df['mf'] > df['cutoff'], df['vc'],
                         np.where(df['mf'] < -df['cutoff'], -df['vc'], 0))
    
    # VFI kiszámítása és simítása
    df['vfi'] = df['vcp'].rolling(length).sum() / df['vave']
    df['vfi'] = df['vfi'].rolling(3).mean()  # SMA alkalmazása
    df['vfima'] = talib.EMA(df['vfi'], timeperiod=signal_length)  # EMA kiszámítása
    
    # Histogram különbség
    df['vfi_diff'] = df['vfi'] - df['vfima']
    
    return df[['vfi', 'vfima', 'vfi_diff']]

def smooth_obv(df, ma_type="SMA", ma_length=14):
    """
    Simított OBV kiszámítása a kiválasztott mozgóátlag alapján.
    """
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]
    
    df['OBV'] = talib.OBV(df['close'], df['volume'])  # TA-Lib OBV kiszámítása

    if ma_type == "SMA":
        df['OBV_Smooth'] = talib.SMA(df['OBV'], timeperiod=ma_length)
    elif ma_type == "EMA":
        df['OBV_Smooth'] = talib.EMA(df['OBV'], timeperiod=ma_length)
    elif ma_type == "WMA":
        df['OBV_Smooth'] = talib.WMA(df['OBV'], timeperiod=ma_length)
    elif ma_type == "SMMA":
        df['OBV_Smooth'] = talib.RMA(df['OBV'], timeperiod=ma_length)
    elif ma_type == "VWMA":
        df['OBV_Smooth'] = df['OBV'].rolling(ma_length).apply(lambda x: np.average(x, weights=df['volume'][-ma_length:]))
    else:
        df['OBV_Smooth'] = df['OBV']  # Ha nincs mozgóátlag, akkor az eredeti OBV-t használja

    return df[['OBV', 'OBV_Smooth']]

def relative_volume_at_time(df, anchor_timeframe='1D', length=10, mode='regular', adjust_realtime=True):
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy().sort_index()
    df.columns = [col.lower() for col in df.columns]

    # Anchor időpontok meghatározása
    anchor_resampled = df.resample(anchor_timeframe).first().index

    # Aktuális anchor időpont minden gyertyához
    df['anchor'] = df.index.to_series().apply(lambda x: anchor_resampled[anchor_resampled <= x][-1])

    # Offset (másodpercben) kiszámítása az anchor-től minden gyertyára
    df['offset'] = (pd.Series(df.index, index=df.index) - df['anchor']).dt.total_seconds()

    relative_volumes = []

    for idx, row in df.iterrows():
        current_offset = row['offset']
        current_anchor = row['anchor']

        # Elmúlt periódusok anchorjai
        past_anchors = anchor_resampled[anchor_resampled < current_anchor][-length:]

        past_volumes = []

        for past_anchor in past_anchors:
            target_time = past_anchor + pd.Timedelta(seconds=current_offset)

            # Megkeressük a pontos vagy legközelebb előtti gyertyát
            past_bars = df[df.index <= target_time]
            if past_bars.empty:
                continue
            matched_time = past_bars.index[-1]

            if mode == 'regular':
                past_volumes.append(df.loc[matched_time]['volume'])

            elif mode == 'cumulative':
                # Az anchor időpontjától a target_time-ig összeadjuk a volumeneket minden múltbeli periódusnál
                cumulative_volume = df[(df.index >= past_anchor) & (df.index <= target_time)]['volume'].sum()
                past_volumes.append(cumulative_volume)

        avg_past_volume = np.mean(past_volumes) if past_volumes else np.nan

        # Aktuális volumen számítása
        if mode == 'regular':
            current_volume = row['volume']
        elif mode == 'cumulative':
            # A jelenlegi anchor időpontjától az aktuális offsetig tartó volumenösszegzés
            current_volume = df[(df.index >= current_anchor) & (df.index <= idx)]['volume'].sum()

        # Realtime adjust (opcionális, ha az utolsó gyertya még nem zárult le)
        if adjust_realtime and (idx == df.index[-1]):
            elapsed_ratio = (row['offset'] + (df.index[1] - df.index[0]).total_seconds()) / (
                df['offset'].max() + (df.index[1] - df.index[0]).total_seconds()
            )
            if elapsed_ratio > 0:
                current_volume /= elapsed_ratio

        relative_volume = current_volume / avg_past_volume if avg_past_volume else np.nan
        relative_volumes.append(relative_volume)

    df['relative_volume_ratio'] = relative_volumes
    df.drop(columns=['anchor', 'offset','open','high','low','close','volume','close_time'], inplace=True)
    df['relative_volume_ratio'].fillna(1, inplace=True)
    df.rename(columns={"relative_volume_ratio": f"relative_volume_ratio_{mode}"}, inplace=True)
    return df

def adaptive_trend_finder(df,mode='shortterm'):
    if mode == "longterm":
        period=(300, 50, 1200)

    elif mode == "shortterm":
        period=(20, 200, 10)

    """
    Adaptive Trend Finder

    :param df: Input DataFrame with OHLC data.
    :param period: Tuple with (start, end, step) for periods.
    :return: DataFrame with trend indicators for each candle.
    """

    df = df.copy()
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df.columns = [col.lower() for col in df.columns]
    results = []
    periods = list(range(*period))

    log_close = np.log(df['close'])

    for idx in range(len(df)):
        best_pearson = -np.inf
        best_period = None
        best_slope = None
        best_intercept = None
        best_deviation = None

        for p in periods:
            if idx + 1 >= p:
                y = log_close.iloc[idx + 1 - p: idx + 1]
                x = np.arange(1, len(y) + 1)

                slope, intercept, r_value, _, _ = linregress(x, y)
                deviation = np.std(y - (intercept + slope * x))

                if abs(r_value) > best_pearson:
                    best_pearson = abs(r_value)
                    best_period = p
                    best_slope = slope
                    best_intercept = intercept
                    best_deviation = deviation

        if best_period is not None:
            results.append({
                'open_time': df.index[idx],
                'Best_Period': best_period,
                'Trend_Slope': best_slope,
                'Trend_Intercept': best_intercept,
                'Trend_PearsonR': best_pearson,
                'Trend_Deviation': best_deviation,
            })

    if not results:
        final_df = None
    
    else:
        final_df = pd.DataFrame(results).set_index('open_time')
        # Normalization for Confidence_Score
        final_df['Pearson_Normalized'] = (final_df['Trend_PearsonR'] - final_df['Trend_PearsonR'].min()) / (final_df['Trend_PearsonR'].max() - final_df['Trend_PearsonR'].min())
        final_df['Deviation_Inverted'] = 1 - (final_df['Trend_Deviation'] - final_df['Trend_Deviation'].min()) / (final_df['Trend_Deviation'].max() - final_df['Trend_Deviation'].min())

        # Confidence Score
        final_df['Confidence_Score'] = final_df['Pearson_Normalized'] * final_df['Deviation_Inverted']

        # Drop helper columns
        final_df.drop(columns=['Pearson_Normalized', 'Deviation_Inverted'], inplace=True)
        final_df.rename(columns={"Trend_Slope": f"Trend_Slope_{mode}" , "Confidence_Score": f"Confidence_Score_{mode}","Trend_Deviation": f"Trend_Deviation_{mode}"}, inplace=True)

    return final_df

def macd(df, fastperiod=12, slowperiod=26, signalperiod=9):
    df = df.copy()
    macd, signal, hist = talib.MACD(df['close'], fastperiod=fastperiod, slowperiod=slowperiod, signalperiod=signalperiod)
    df['macd'] = macd
    df['macd_signal'] = signal
    df['macd_hist'] = hist
    return df[['macd', 'macd_signal', 'macd_hist']]

def garch_volatility(df, alpha_start=0.10, beta_start=0.80, ema_length=20, tolerance=0.0001, max_iter=100):
    # MINDEN oszlop kisbetűsítése, hogy biztosan egységes legyen
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    price = df['close']
    ema = price.ewm(span=ema_length, adjust=False).mean()

    variance = np.full_like(price, np.nan, dtype=np.float64)
    volatility = np.full_like(price, np.nan, dtype=np.float64)

    for i in range(1, len(price)):
        prev_variance = variance[i - 1] if i > 1 else 0
        new_variance = alpha_start * (price[i] - ema[i]) ** 2 + beta_start * prev_variance
        variance[i] = new_variance
        volatility[i] = np.sqrt(new_variance)

    df['garch_volatility'] = volatility
    # print(f"garch min: {df['garch_volatility'].min()}, max: {df['garch_volatility'].max()}")
    return df[['garch_volatility']]

def ema(df, ema_length=120):
    """
    Exponenciális Mozgó Átlag (EMA) indikátor számítása.

    :param df: pandas DataFrame, amely tartalmazza a 'close' oszlopot
    :param ema_length: EMA számítási periódusa (alapértelmezett: 14)
    :return: DataFrame az EMA értékekkel és egy alapvető jelzéssel
    """
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    df['ema'] = talib.EMA(df['close'], timeperiod=ema_length)
    df['ema_signal'] = df['close'] > df['ema']  # Egyszerű jel: ár az EMA fölött
    df['ema_entry'] = (df['close'].shift(1) < df['ema'].shift(1)) & (df['close'] > df['ema'])

    return df[['ema', 'ema_signal', 'ema_entry']]

def williams_r(df, period=14):
    """
    Williams %R indikátor számítása.

    :param df: Input DataFrame with 'high', 'low', 'close' oszlopokkal
    :param period: A számításhoz használt periódus hossza (default=14)
    :return: DataFrame a Williams %R értékekkel és egy egyszerű jelzéssel
    """
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    df['williams_r'] = talib.WILLR(df['high'], df['low'], df['close'], timeperiod=period)

    # Jelzés: ha Williams%R > -80, akkor long szignál (oversold recovery kezdete)
    df['williamsr_signal'] = df['williams_r'] > -80

    return df[['williams_r', 'williamsr_signal']]

def awesome_oscillator(df):
    """
    Awesome Oscillator (AO) indikátor számítása.
    AO = (5-periódusú SMA (High+Low)/2) - (34-periódusú SMA (High+Low)/2)

    :param df: DataFrame (high, low kötelező)
    :return: DataFrame az AO értékkel és egyszerű szignálokkal
    """
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    median_price = (df['high'] + df['low']) / 2
    df['ao'] = talib.SMA(median_price, timeperiod=5) - talib.SMA(median_price, timeperiod=34)
    
    # Alapszignál: pozitív vagy negatív érték
    df['ao_signal'] = df['ao'] > 0

    return df[['ao', 'ao_signal']]

def ultimate_oscillator(df, timeperiod1=7, timeperiod2=14, timeperiod3=28):
    """
    Ultimate Oscillator (UO) számítása.
    Egy kombinált indikátor rövidebb, közepes és hosszú időszakok alapján.

    :param df: DataFrame (high, low, close kötelező)
    :return: DataFrame a UO értékekkel és szignállal
    """
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    df['uo'] = talib.ULTOSC(df['high'], df['low'], df['close'], 
                             timeperiod1=timeperiod1, 
                             timeperiod2=timeperiod2, 
                             timeperiod3=timeperiod3)
    
    # Alapszignál: ha UO > 50, bullish
    df['uo_signal'] = df['uo'] > 50

    return df[['uo', 'uo_signal']]

def money_flow_index(df, timeperiod=14):
    """
    Money Flow Index (MFI) számítása.
    Volumen-alapú oszcillátor.

    :param df: DataFrame (high, low, close, volume kötelező)
    :return: DataFrame az MFI értékkel és szignállal
    """
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    df['mfi'] = talib.MFI(df['high'], df['low'], df['close'], df['volume'], timeperiod=timeperiod)

    # Alapszignál: MFI > 50 pozitív
    df['mfi_signal'] = df['mfi'] > 50

    return df[['mfi', 'mfi_signal']]

def accumulation_distribution(df):
    """
    Accumulation/Distribution (A/D Line) számítása.

    :param df: DataFrame (kötelező: high, low, close, volume oszlopokkal)
    :return: DataFrame az AD értékkel
    """
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]

    high = df['high']
    low = df['low']
    close = df['close']
    volume = df['volume']

    # Money Flow Multiplier
    mf_multiplier = np.where(
        (high == low),
        0,
        ((2 * close - low - high) / (high - low))
    )

    # Money Flow Volume
    mf_volume = mf_multiplier * volume

    # Cumulative AD line
    df['ad'] = mf_volume.cumsum()

    return df[['ad']]

def fear_and_greed_index(df):
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]
    # Biztonsági ellenőrzés az időzónára
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')

    fgi_path = "data/Fear&GreedIndex old_4h.csv"
    if not os.path.exists(fgi_path):
        raise FileNotFoundError(f"Nem található a fájl: {fgi_path}")

    # FGI fájl beolvasása
    fgi_df = pd.read_csv(fgi_path, parse_dates=["open_time"])
    fgi_df = fgi_df.set_index("open_time")
    if fgi_df.index.tz is None:
        fgi_df.index = fgi_df.index.tz_localize('UTC')

    # Index alapú illesztés
    df = df.join(fgi_df[["fear_greed_value"]], how="left")
    df = df.rename(columns={"fear_greed_value": "fgi"})

    # Egyszerű jelzés logika
    df["fgi_signal"] = df["fgi"] >= 40
    return df[["fgi", "fgi_signal"]]

def nwr_on_fgi(df):
    out = nwr(df, src_col='fgi')
    return out.rename(columns={
        'nwr_yhat1': 'nwr_fgi_yhat1',
        'nwr_yhat2': 'nwr_fgi_yhat2',
        'nwr_signal': 'nwr_fgi_signal',
        'nwr_entry': 'nwr_fgi_entry',
    })

def nwr_on_ad(df):
    out = nwr(df, src_col='ad')
    return out.rename(columns={
        'nwr_yhat1': 'nwr_ad_yhat1',
        'nwr_yhat2': 'nwr_ad_yhat2',
        'nwr_signal': 'nwr_ad_signal',
        'nwr_entry': 'nwr_ad_entry',
    })

def nwr_on_wt(df):
    out = nwr(df, src_col='wt_2')
    return out.rename(columns={
        'nwr_yhat1': 'nwr_wt_yhat1',
        'nwr_yhat2': 'nwr_wt_yhat2',
        'nwr_signal': 'nwr_wt_signal',
        'nwr_entry': 'nwr_wt_entry',
    })

def nwr_on_rsi(df):
    out = nwr(df, src_col='rsi')
    return out.rename(columns={
        'nwr_yhat1': 'nwr_rsi_yhat1',
        'nwr_yhat2': 'nwr_rsi_yhat2',
        'nwr_signal': 'nwr_rsi_signal',
        'nwr_entry': 'nwr_rsi_entry',
    })

def lorentzian_predictions(df, pairname="BTCUSDT", timeframe="4H"):
    """
    Lorentzian osztályozó predikcióinak betöltése a megadott páros és időtáv alapján.
    A CSV fájlnak a következő formátumban kell elérhetőnek lennie:
    'data/ml_output/{pair}_{timeframe}_lorentzian_result.csv'

    :param df: OHLCV DataFrame az idősor indexével (UTC)
    :param pairname: Pár neve (pl. 'BTCUSDT')
    :param timeframe: Időtáv (pl. '4H')
    :return: DataFrame két oszloppal: 'lorentz_pred', 'lorentz_signal'
    """
    filename = f"data/{pairname}_{timeframe}_lorentzian_result.csv"
    if not os.path.exists(filename):
        raise FileNotFoundError(f"A Lorentzian eredmény fájl nem található: {filename}")

    lorentz_df = pd.read_csv(filename, parse_dates=['open_time'])
    lorentz_df['open_time'] = lorentz_df['open_time'].dt.tz_localize('UTC')
    lorentz_df.set_index('open_time', inplace=True)

    result = pd.DataFrame(index=df.index)
    result['lorentz_pred'] = lorentz_df['prediction'].reindex(df.index)
    # Küszöbös logikai érték: csak ha prediction > threshold, akkor True, különben False, NaN marad
    result['lorentz_signal'] = result['lorentz_pred'].apply(
        lambda x: True if pd.notna(x) and x >= 0 else (False if pd.notna(x) else np.nan)
    )
    return result

import pandas as pd

def rsi_divergence(df, len_fast=5, len_slow=14):
    df = df.copy()
    df.columns = [col.lower() for col in df.columns]
    src_fast = df['close']
    src_slow = df['close']

    # Fast RSI
    delta_fast = src_fast.diff()
    up_fast = delta_fast.clip(lower=0)
    down_fast = -delta_fast.clip(upper=0)

    roll_up_fast = up_fast.rolling(len_fast, min_periods=1).mean()
    roll_down_fast = down_fast.rolling(len_fast, min_periods=1).mean()
    rsi_fast = 100 - (100 / (1 + roll_up_fast / (roll_down_fast + 1e-10)))

    # Slow RSI
    delta_slow = src_slow.diff()
    up_slow = delta_slow.clip(lower=0)
    down_slow = -delta_slow.clip(upper=0)

    roll_up_slow = up_slow.rolling(len_slow, min_periods=1).mean()
    roll_down_slow = down_slow.rolling(len_slow, min_periods=1).mean()
    rsi_slow = 100 - (100 / (1 + roll_up_slow / (roll_down_slow + 1e-10)))

    # Divergence
    divergence = rsi_fast - rsi_slow

    result = pd.DataFrame(index=df.index)

    # Signal: True if divergence >= 0, else False
    rsi_divergence_signal = divergence >= 0

    result = pd.DataFrame({
        'rsi_div_fast': rsi_fast,
        'rsi_div_slow': rsi_slow,
        'rsi_divergence': divergence,
        'rsi_divergence_signal': rsi_divergence_signal
    }, index=df.index)
    return result
