# api/charges.py
"""
Brokerage & margin endpoints with retry logic and centralized configuration,
leveraging custom exceptions for precise error handling.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Union

import httpx
import requests
from constants import (
    UPSTOX_CHARGES_URL,
    UPSTOX_MARGIN_URL,
    DEFAULT_ACCEPT_HEADER,
    DEFAULT_API_VERSION,
    API_TIMEOUT_SECONDS,
    API_RETRY_ATTEMPTS,
    API_RETRY_DELAY_SECONDS,
    RATE_LIMIT_BUFFER_SECONDS,
    FALLBACK_BROKERAGE_CHARGE,
    FALLBACK_MARGIN_REQUIREMENT
)
from api.exceptions import (
    BrokerageCalculationError,
    MarginCalculationError,
    RateLimitError,
    NetworkError
)

logger = logging.getLogger(__name__)

class ChargesService:
    """Enhanced charges service with async support and robust error handling."""
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.fallback_charge = FALLBACK_BROKERAGE_CHARGE
        self._request_count = 0
        self._last_request_time = 0.0
        self._headers = {
            'Accept': DEFAULT_ACCEPT_HEADER,
            'Api-Version': DEFAULT_API_VERSION,
            'Authorization': f'Bearer {access_token}'
        }

    def _rate_limit(self):
        """Enforce simple rate-limit between successive calls."""
        now = time.time()
        delta = now - self._last_request_time
        if delta < RATE_LIMIT_BUFFER_SECONDS:
            sleep_sec = RATE_LIMIT_BUFFER_SECONDS - delta
            time.sleep(sleep_sec)
        self._last_request_time = time.time()
        self._request_count += 1

    async def calculate_brokerage_async(
        self,
        instruments: List[Dict],
        return_breakdown: bool = False
    ) -> List[Union[float, Dict]]:
        """Async brokerage calculation for multiple instruments."""
        tasks = [
            self._calculate_single_brokerage_async(instr, return_breakdown)
            for instr in instruments
        ]
        return await asyncio.gather(*tasks)

    async def _calculate_single_brokerage_async(
        self,
        instrument: Dict,
        return_breakdown: bool
    ) -> Union[float, Dict]:
        for attempt in range(API_RETRY_ATTEMPTS):
            try:
                params = {
                    'instrument_token': instrument['instrument_key'],
                    'quantity': str(instrument['quantity']),
                    'product': instrument['product'],
                    'transaction_type': instrument['transaction_type'],
                    'price': str(instrument['price'])
                }
                async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
                    resp = await client.get(
                        UPSTOX_CHARGES_URL,
                        headers=self._headers,
                        params=params
                    )
                resp.raise_for_status()
                data = resp.json()
                if data.get('status') != 'success':
                    raise BrokerageCalculationError(f"API returned status {data.get('status')}")
                charges = data['data']['charges']
                if return_breakdown:
                    return {k: round(v,2) for k,v in charges.items()}
                return round(charges['total'],2)
            except httpx.RequestError as net_err:
                logger.warning(f"Network error on async brokerage attempt {attempt+1}: {net_err}")
                if attempt < API_RETRY_ATTEMPTS-1:
                    await asyncio.sleep(API_RETRY_DELAY_SECONDS)
                    continue
                raise NetworkError(str(net_err))
            except BrokerageCalculationError:
                raise
            except Exception as err:
                logger.warning(f"Async brokerage attempt {attempt+1} failed: {err}")
                if attempt < API_RETRY_ATTEMPTS-1:
                    await asyncio.sleep(API_RETRY_DELAY_SECONDS)
        # fallback
        logger.warning(f"Using fallback charge {self.fallback_charge}")
        if return_breakdown:
            return {'total': round(self.fallback_charge,2), 'source':'fallback'}
        return round(self.fallback_charge,2)

    def calculate_brokerage(
        self,
        instruments: List[Dict],
        return_breakdown: bool = False
    ) -> List[Union[float, Dict]]:
        """Sync brokerage calculation with retries and fallback."""
        results = []
        for instr in instruments:
            self._rate_limit()
            try:
                result = self._calculate_single_brokerage_sync(instr, return_breakdown)
            except Exception as err:
                logger.error(f"Brokerage sync failed for {instr}: {err}")
                result = (round(self.fallback_charge,2) if not return_breakdown
                          else {'total': round(self.fallback_charge,2), 'source':'fallback'})
            results.append(result)
        return results

    def _calculate_single_brokerage_sync(
        self,
        instrument: Dict,
        return_breakdown: bool
    ) -> Union[float, Dict]:
        for attempt in range(API_RETRY_ATTEMPTS):
            try:
                params = {
                    'instrument_token': instrument['instrument_key'],
                    'quantity': str(instrument['quantity']),
                    'product': instrument['product'],
                    'transaction_type': instrument['transaction_type'],
                    'price': str(instrument['price'])
                }
                resp = requests.get(
                    UPSTOX_CHARGES_URL,
                    headers=self._headers,
                    params=params,
                    timeout=API_TIMEOUT_SECONDS
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get('status') != 'success':
                    raise BrokerageCalculationError(f"API returned status {data.get('status')}")
                charges = data['data']['charges']
                if return_breakdown:
                    return {k: round(v,2) for k,v in charges.items()}
                return round(charges['total'],2)
            except requests.RequestException as net_err:
                logger.warning(f"Network error attempt {attempt+1}: {net_err}")
                if attempt < API_RETRY_ATTEMPTS-1:
                    time.sleep(API_RETRY_DELAY_SECONDS)
                    continue
                raise NetworkError(str(net_err))
            except BrokerageCalculationError:
                raise
            except Exception as err:
                logger.warning(f"Brokerage sync attempt {attempt+1} failed: {err}")
                if attempt < API_RETRY_ATTEMPTS-1:
                    time.sleep(API_RETRY_DELAY_SECONDS)
        raise BrokerageCalculationError("All retries failed for brokerage calculation")

    async def calculate_margin_requirements_async(
        self,
        instruments: List[Dict]
    ) -> Dict:
        """Async margin calculation."""
        for attempt in range(API_RETRY_ATTEMPTS):
            try:
                payload = {'instruments': instruments}
                async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
                    resp = await client.post(
                        UPSTOX_MARGIN_URL,
                        headers=self._headers,
                        json=payload
                    )
                resp.raise_for_status()
                data = resp.json()
                if data.get('status') != 'success':
                    raise MarginCalculationError(f"API status {data.get('status')}")
                return data['data']
            except httpx.RequestError as net_err:
                logger.warning(f"Network error on async margin attempt {attempt+1}: {net_err}")
                if attempt < API_RETRY_ATTEMPTS-1:
                    await asyncio.sleep(API_RETRY_DELAY_SECONDS)
                    continue
                raise NetworkError(str(net_err))
            except MarginCalculationError:
                raise
            except Exception as err:
                logger.warning(f"Async margin attempt {attempt+1} failed: {err}")
                if attempt < API_RETRY_ATTEMPTS-1:
                    await asyncio.sleep(API_RETRY_DELAY_SECONDS)
        # fallback
        total = sum(inst['quantity']*inst['price']*FALLBACK_MARGIN_REQUIREMENT for inst in instruments)
        return {'total': round(total,2), 'source':'fallback'}

    def calculate_margin_requirements(
        self,
        instruments: List[Dict]
    ) -> Dict:
        """Sync margin calculation with retries and fallback."""
        self._rate_limit()
        for attempt in range(API_RETRY_ATTEMPTS):
            try:
                payload = {'instruments': instruments}
                resp = requests.post(
                    UPSTOX_MARGIN_URL,
                    headers=self._headers,
                    json=payload,
                    timeout=API_TIMEOUT_SECONDS
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get('status') != 'success':
                    raise MarginCalculationError(f"API status {data.get('status')}")
                return data['data']
            except requests.RequestException as net_err:
                logger.warning(f"Network error attempt {attempt+1}: {net_err}")
                if attempt < API_RETRY_ATTEMPTS-1:
                    time.sleep(API_RETRY_DELAY_SECONDS)
                    continue
                raise NetworkError(str(net_err))
            except MarginCalculationError:
                raise
            except Exception as err:
                logger.warning(f"Sync margin attempt {attempt+1} failed: {err}")
                if attempt < API_RETRY_ATTEMPTS-1:
                    time.sleep(API_RETRY_DELAY_SECONDS)
        # fallback
        total = sum(inst['quantity']*inst['price']*FALLBACK_MARGIN_REQUIREMENT for inst in instruments)
        raise MarginCalculationError("All retries failed for margin calculation")

    def get_request_stats(self) -> Dict:
        """Return API usage stats and fallback usage."""
        return {'total_requests': self._request_count}

# Backward compatibility

def get_upstox_charges(
    access_token: str,
    instruments: List[Dict],
    fallback_charge: float = FALLBACK_BROKERAGE_CHARGE,
    return_breakdown: bool = False
) -> List[Union[float, Dict]]:
    service = ChargesService(access_token)
    service.fallback_charge = fallback_charge
    return service.calculate_brokerage(instruments, return_breakdown)
