import pandas as pd
from shared.tools import load_function
import numpy as np
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
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

log_dir_path = Path('logs')
log_dir_path.mkdir(parents=True, exist_ok=True)
file_handler = logging.FileHandler(log_dir_path / 'indicator.log')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

class Indicator:
    _instances = {}

    def __new__(cls, name, *args, **kwargs):
        if name in cls._instances:
            logger.debug(f"Reusing existing Indicator instance for '{name}'.")
            return cls._instances[name]
        logger.debug(f"Creating new Indicator instance for '{name}'.")
        instance = super().__new__(cls)
        cls._instances[name] = instance
        return instance

    def __init__(self, name, data_provider):
        if hasattr(self, "_initialized"):
            return
        self.name = name
        self.data_provider = data_provider
        self.func = load_function(name)
        self._initialized = True
        logger.debug(f"Indicator '{self.name}' initialized.")
