# api/charges.py
"""
Brokerage & margin endpoints with retry logic and centralized configuration,
leveraging custom exceptions for precise error handling.
"""

import asyncio
import logging
import time

import httpx
import requests
from fastapi import Query

# from constants import (
#     upstox_charges_url,
#     UPSTOX_MARGIN_URL,
#     DEFAULT_ACCEPT_HEADER,
#     DEFAULT_API_VERSION,
#     API_TIMEOUT_SECONDS,
#     API_RETRY_ATTEMPTS,
#     API_RETRY_DELAY_SECONDS,
#     RATE_LIMIT_BUFFER_SECONDS,
#     FALLBACK_BROKERAGE_CHARGE,
#     FALLBACK_MARGIN_REQUIREMENT
# )
from ...config.settings import settings

upstox_charges_url = settings.upstox_charges_url
from ...utils.exceptions import BrokerageCalculationError, MarginCalculationError, NetworkError

logger = logging.getLogger(__name__)


class ChargesService:
    """Enhanced charges service with async support and robust error handling."""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.fallback_charge = Query(settings.FALLBACK_BROKERAGE_CHARGE)
        self._request_count = 0
        self._last_request_time = 0.0
        self._headers = {
            "Accept": Query(settings.default_accept_header),
            "Api-Version": Query(settings.default_api_version),
            "Authorization": f"Bearer {access_token}",
        }

    def _rate_limit(self):
        """Enforce simple rate-limit between successive calls."""
        now = time.time()
        delta = now - self._last_request_time
        if delta < Query(settings.rate_limit_buffer_seconds):
            sleep_sec = Query(settings.rate_limit_buffer_seconds) - delta
            time.sleep(sleep_sec)
        self._last_request_time = time.time()
        self._request_count += 1

    async def calculate_brokerage_async(
        self, instruments: list[dict], return_breakdown: bool = False
    ) -> list[float | dict]:
        """Async brokerage calculation for multiple instruments."""
        tasks = [
            self._calculate_single_brokerage_async(instr, return_breakdown) for instr in instruments
        ]
        return await asyncio.gather(*tasks)

    async def _calculate_single_brokerage_async(
        self, instrument: dict, return_breakdown: bool
    ) -> float | dict:
        for attempt in range(Query(settings.api_retry_attempts)):
            try:
                params = {
                    "instrument_token": instrument["instrument_key"],
                    "quantity": str(instrument["quantity"]),
                    "product": instrument["product"],
                    "transaction_type": instrument["transaction_type"],
                    "price": str(instrument["price"]),
                }
                async with httpx.AsyncClient(timeout=Query(settings.api_timeout_seconds)) as client:
                    resp = await client.get(
                        upstox_charges_url, headers=self._headers, params=params
                    )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "success":
                    raise BrokerageCalculationError(f"API returned status {data.get('status')}")
                charges = data["data"]["charges"]
                if return_breakdown:
                    return {k: round(v, 2) for k, v in charges.items()}
                return round(charges["total"], 2)
            except httpx.RequestError as net_err:
                logger.warning(f"Network error on async brokerage attempt {attempt + 1}: {net_err}")
                if attempt < Query(settings.api_retry_attempts) - 1:
                    await asyncio.sleep(Query(settings.api_retry_delay_seconds))
                    continue
                raise NetworkError(str(net_err))
            except BrokerageCalculationError:
                raise
            except Exception as err:
                logger.warning(f"Async brokerage attempt {attempt + 1} failed: {err}")
                if attempt < Query(settings.api_retry_attempts) - 1:
                    await asyncio.sleep(Query(settings.api_retry_delay_seconds))
        # fallback
        logger.warning(f"Using fallback charge {self.fallback_charge}")
        if return_breakdown:
            return {"total": round(self.fallback_charge, 2), "source": "fallback"}
        return round(self.fallback_charge, 2)

    def calculate_brokerage(
        self, instruments: list[dict], return_breakdown: bool = False
    ) -> list[float | dict]:
        """Sync brokerage calculation with retries and fallback."""
        results = []
        for instr in instruments:
            self._rate_limit()
            try:
                result = self._calculate_single_brokerage_sync(instr, return_breakdown)
            except Exception as err:
                logger.error(f"Brokerage sync failed for {instr}: {err}")
                result = (
                    round(self.fallback_charge, 2)
                    if not return_breakdown
                    else {"total": round(self.fallback_charge, 2), "source": "fallback"}
                )
            results.append(result)
        return results

    def _calculate_single_brokerage_sync(
        self, instrument: dict, return_breakdown: bool
    ) -> float | dict:
        for attempt in range(Query(settings.api_retry_attempts)):
            try:
                params = {
                    "instrument_token": instrument["instrument_key"],
                    "quantity": str(instrument["quantity"]),
                    "product": instrument["product"],
                    "transaction_type": instrument["transaction_type"],
                    "price": str(instrument["price"]),
                }
                resp = requests.get(
                    upstox_charges_url,
                    headers=self._headers,
                    params=params,
                    timeout=Query(settings.api_timeout_seconds),
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "success":
                    raise BrokerageCalculationError(f"API returned status {data.get('status')}")
                charges = data["data"]["charges"]
                if return_breakdown:
                    return {k: round(v, 2) for k, v in charges.items()}
                return round(charges["total"], 2)
            except requests.RequestException as net_err:
                logger.warning(f"Network error attempt {attempt + 1}: {net_err}")
                if attempt < Query(settings.api_retry_attempts) - 1:
                    time.sleep(Query(settings.api_retry_delay_seconds))
                    continue
                raise NetworkError(str(net_err))
            except BrokerageCalculationError:
                raise
            except Exception as err:
                logger.warning(f"Brokerage sync attempt {attempt + 1} failed: {err}")
                if attempt < Query(settings.api_retry_attempts) - 1:
                    time.sleep(Query(settings.api_retry_delay_seconds))
        raise BrokerageCalculationError("All retries failed for brokerage calculation")

    async def calculate_margin_requirements_async(self, instruments: list[dict]) -> dict:
        """Async margin calculation."""
        for attempt in range(Query(settings.api_retry_attempts)):
            try:
                payload = {"instruments": instruments}
                async with httpx.AsyncClient(timeout=Query(settings.api_timeout_seconds)) as client:
                    resp = await client.post(
                        Query(settings.upstox_margin_url), headers=self._headers, json=payload
                    )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "success":
                    raise MarginCalculationError(f"API status {data.get('status')}")
                return data["data"]
            except httpx.RequestError as net_err:
                logger.warning(f"Network error on async margin attempt {attempt + 1}: {net_err}")
                if attempt < Query(settings.api_retry_attempts) - 1:
                    await asyncio.sleep(Query(settings.api_retry_delay_seconds))
                    continue
                raise NetworkError(str(net_err))
            except MarginCalculationError:
                raise
            except Exception as err:
                logger.warning(f"Async margin attempt {attempt + 1} failed: {err}")
                if attempt < Query(settings.api_retry_attempts) - 1:
                    await asyncio.sleep(Query(settings.api_retry_delay_seconds))
        # fallback
        total = sum(inst["quantity"] * inst["price"] for inst in instruments)
        return {"total": round(total, 2), "source": "fallback"}

    def calculate_margin_requirements(self, instruments: list[dict]) -> dict:
        """Sync margin calculation with retries and fallback."""
        self._rate_limit()
        for attempt in range(Query(settings.api_retry_attempts)):
            try:
                payload = {"instruments": instruments}
                resp = requests.post(
                    Query(settings.upstox_margin_url),
                    headers=self._headers,
                    json=payload,
                    timeout=Query(settings.api_timeout_seconds),
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "success":
                    raise MarginCalculationError(f"API status {data.get('status')}")
                return data["data"]
            except requests.RequestException as net_err:
                logger.warning(f"Network error attempt {attempt + 1}: {net_err}")
                if attempt < Query(settings.api_retry_attempts) - 1:
                    time.sleep(Query(settings.api_retry_delay_seconds))
                    continue
                raise NetworkError(str(net_err))
            except MarginCalculationError:
                raise
            except Exception as err:
                logger.warning(f"Sync margin attempt {attempt + 1} failed: {err}")
                if attempt < Query(settings.api_retry_attempts) - 1:
                    time.sleep(Query(settings.api_retry_delay_seconds))
        # fallback
        total = sum(inst["quantity"] * inst["price"] for inst in instruments)
        raise MarginCalculationError("All retries failed for margin calculation")

    def get_request_stats(self) -> dict:
        """Return API usage stats and fallback usage."""
        return {"total_requests": self._request_count}


# Backward compatibility


def get_upstox_charges(
    access_token: str,
    instruments: list[dict],
    fallback_charge: float = Query(settings.FALLBACK_BROKERAGE_CHARGE),
    return_breakdown: bool = False,
) -> list[float | dict]:
    service = ChargesService(access_token)
    service.fallback_charge = fallback_charge
    return service.calculate_brokerage(instruments, return_breakdown)
