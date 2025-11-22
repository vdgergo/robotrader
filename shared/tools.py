import importlib
import pandas as pd
import inspect
import hashlib
from datetime import datetime, timezone, timedelta


def load_function(name):
    module = importlib.import_module('analyzer.indicator_defs')
    if hasattr(module, name):
        return getattr(module, name)
    raise ImportError(f"Nincs ilyen függvény az indicator_defs-ben: {name}")

def get_default_params(name):
    default_params = {}
    module = importlib.import_module('analyzer.indicator_defs')
    if hasattr(module, name):
        sig = inspect.signature(getattr(module, name))
        for pname, param in sig.parameters.items():
            if param.default is not inspect._empty:
                default_params[pname] = param.default
        return default_params
        
    else:
        raise ImportError(f"Nincs ilyen függvény az indicator_defs-ben: {name}")

def floor_to_nh(inp, n_hours):
    if not isinstance(inp,datetime):
        dt = pd.to_datetime(int(inp), unit="s", utc=True)
    else:
        dt = inp
    h = (dt.hour // n_hours) * n_hours
    return dt.replace(hour=h, minute=0, second=0, microsecond=0)

def interval_to_timedelta(interval):
    unit = interval[-1].lower()
    value = int(interval[:-1])

    if unit == 'm':
        return timedelta(minutes=value)
    elif unit == 'h':
        return timedelta(hours=value)
    elif unit == 'd':
        return timedelta(days=value)
    elif unit == 'w':
        return timedelta(weeks=value)
    else:
        raise ValueError(f"Nem támogatott időegység: {unit}")

def generate_params_hash(params):
    if not params:
        return "default"
    parts = [f"{k}={v}" for k, v in sorted(params.items())]
    raw_string = "_".join(parts)
    hash_digest = hashlib.md5(raw_string.encode()).hexdigest()[:8]  # rövid hash
    return hash_digest
    
def is_all_float_zero(df):
    float_cols = df.select_dtypes(include='float').columns
    return (df[float_cols] == 0).values.all()