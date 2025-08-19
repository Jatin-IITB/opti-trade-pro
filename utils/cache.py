# utils/cache.py

import logging
from typing import Any, Callable, Dict, Optional
import pandas as pd

logger = logging.getLogger(__name__)

class SpotDataCache:
    """General-purpose LRU cache for DataFrame objects with logging and diagnostics."""
    def __init__(self, max_cache_size: int = 20):
        self.cache: Dict[str, pd.DataFrame] = {}
        self.order = []
        self.max_cache_size = max_cache_size
        self.stats = {"hits": 0, "misses": 0}

    def get(
        self, 
        underlying_key: str, 
        start_date: str, 
        end_date: str,
        access_token: str,
        interval_minutes: int,
        fetch_func: Callable[..., pd.DataFrame] = None, 
    ) -> pd.DataFrame:
        """Get DataFrame from cache or fetch if not present."""
        key = f"{underlying_key}_{start_date}_{end_date}_{interval_minutes}"
        if key in self.cache:
            self.stats["hits"] += 1
            logger.info(f"CACHE HIT: {key}")
            # Move to most recent
            self.order.remove(key)
            self.order.append(key)
            return self.cache[key].copy()
        else:
            self.stats["misses"] += 1
            logger.info(f"CACHE MISS: {key}")
            spot_data= fetch_func(
                access_token=access_token,
                instrument_token=underlying_key,
                from_date=start_date,
                to_date=end_date,
                interval=str(interval_minutes),
                unit="minutes"
            )
            self.cache[key] = spot_data.copy()
            self.order.append(key)
            self._cleanup()
            return spot_data

    def _cleanup(self):
        while len(self.order) > self.max_cache_size:
            oldest = self.order.pop(0)
            del self.cache[oldest]
            logger.info(f"CACHE CLEANED: Evicted {oldest}")

    def print_stats(self):
        total = self.stats["hits"] + self.stats["misses"]
        hit_rate = (self.stats["hits"] / total) * 100 if total > 0 else 0
        logger.info(
            f"Cache stats: {self.stats['hits']} hits, {self.stats['misses']} misses, {hit_rate:.1f}% hit rate"
        )
        logger.info(f"Cache contains {len(self.cache)} items")
