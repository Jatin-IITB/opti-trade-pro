# src/options_trading/services/websocket_manager.py
"""
Enterprise-grade WebSocket manager for real-time dashboard updates.
Handles client connections, subscriptions, and broadcasts with high reliability.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for a client"""

    def __init__(self, websocket: WebSocket, client_id: str):
        self.websocket = websocket
        self.client_id = client_id
        self.subscriptions: set[str] = set()
        self.connected_at = datetime.now()
        self.last_activity = datetime.now()
        self.is_active = True

    async def send_message(self, message: dict[str, Any]) -> bool:
        """Send message to client with error handling"""
        try:
            await self.websocket.send_json(message)
            self.last_activity = datetime.now()
            return True
        except WebSocketDisconnect:
            logger.info(f"Client {self.client_id} disconnected during send")
            self.is_active = False
            return False
        except Exception as e:
            logger.error(f"Failed to send message to {self.client_id}: {e}")
            self.is_active = False
            return False

    def add_subscription(self, symbols: list[str]) -> None:
        """Add symbols to client subscriptions"""
        self.subscriptions.update(symbols)
        self.last_activity = datetime.now()

    def remove_subscription(self, symbols: list[str]) -> None:
        """Remove symbols from client subscriptions"""
        for symbol in symbols:
            self.subscriptions.discard(symbol)
        self.last_activity = datetime.now()

    def is_subscribed_to(self, symbol: str) -> bool:
        """Check if client is subscribed to symbol"""
        return symbol in self.subscriptions

    @property
    def connection_duration(self) -> float:
        """Get connection duration in seconds"""
        return (datetime.now() - self.connected_at).total_seconds()


class WebSocketManager:
    """
    Production-grade WebSocket manager for real-time trading dashboard.
    Handles multiple clients, subscriptions, and reliable message delivery.
    """

    def __init__(self):
        self.connections: dict[str, ConnectionManager] = {}
        self.symbol_subscriptions: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self.broadcast_queue = asyncio.Queue()
        self.cleanup_task = None
        self._stats = {
            "total_connections": 0,
            "active_connections": 0,
            "messages_sent": 0,
            "messages_failed": 0,
        }

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """Accept new WebSocket connection"""
        try:
            await websocket.accept()

            connection = ConnectionManager(websocket, client_id)
            self.connections[client_id] = connection

            self._stats["total_connections"] += 1
            self._stats["active_connections"] += 1

            logger.info(
                f"Client {client_id} connected. Active connections: {self._stats['active_connections']}"
            )

            # Start cleanup task if not running
            if self.cleanup_task is None or self.cleanup_task.done():
                self.cleanup_task = asyncio.create_task(self._periodic_cleanup())

        except Exception as e:
            logger.error(f"Failed to connect client {client_id}: {e}")

    async def disconnect(self, websocket: WebSocket, client_id: str) -> None:
        """Handle client disconnection"""
        try:
            async with self._lock:
                if client_id in self.connections:
                    connection = self.connections[client_id]

                    # Remove all subscriptions
                    for symbol in connection.subscriptions.copy():
                        self.symbol_subscriptions[symbol].discard(client_id)
                        if not self.symbol_subscriptions[symbol]:
                            del self.symbol_subscriptions[symbol]

                    # Remove connection
                    del self.connections[client_id]
                    self._stats["active_connections"] -= 1

                    logger.info(
                        f"Client {client_id} disconnected. Active connections: {self._stats['active_connections']}"
                    )

        except Exception as e:
            logger.error(f"Error during client {client_id} disconnection: {e}")

    async def subscribe_client(self, client_id: str, symbols: list[str]) -> bool:
        """Subscribe client to symbol updates"""
        try:
            if client_id not in self.connections:
                logger.warning(f"Cannot subscribe - client {client_id} not connected")
                return False

            connection = self.connections[client_id]
            connection.add_subscription(symbols)

            # Update symbol subscriptions mapping
            for symbol in symbols:
                self.symbol_subscriptions[symbol].add(client_id)

            logger.debug(f"Client {client_id} subscribed to {symbols}")
            return True

        except Exception as e:
            logger.error(f"Failed to subscribe client {client_id}: {e}")
            return False

    async def unsubscribe_client(self, client_id: str, symbols: list[str]) -> bool:
        """Unsubscribe client from symbol updates"""
        try:
            if client_id not in self.connections:
                return False

            connection = self.connections[client_id]
            connection.remove_subscription(symbols)

            # Update symbol subscriptions mapping
            for symbol in symbols:
                self.symbol_subscriptions[symbol].discard(client_id)
                if not self.symbol_subscriptions[symbol]:
                    del self.symbol_subscriptions[symbol]

            logger.debug(f"Client {client_id} unsubscribed from {symbols}")
            return True

        except Exception as e:
            logger.error(f"Failed to unsubscribe client {client_id}: {e}")
            return False

    async def send_to_client(self, client_id: str, message: dict[str, Any]) -> bool:
        """Send message to specific client"""
        try:
            if client_id not in self.connections:
                return False

            connection = self.connections[client_id]
            success = await connection.send_message(message)

            if success:
                self._stats["messages_sent"] += 1
            else:
                self._stats["messages_failed"] += 1

            return success

        except Exception as e:
            logger.error(f"Failed to send message to client {client_id}: {e}")
            self._stats["messages_failed"] += 1
            return False

    async def broadcast_to_symbol_subscribers(self, symbol: str, message: dict[str, Any]) -> int:
        """Broadcast message to all clients subscribed to a symbol"""
        if symbol not in self.symbol_subscriptions:
            return 0

        client_ids = list(self.symbol_subscriptions[symbol])
        success_count = 0

        # Send to all subscribers concurrently
        tasks = []
        for client_id in client_ids:
            task = asyncio.create_task(self.send_to_client(client_id, message))
            tasks.append(task)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count = sum(1 for result in results if result is True)

        logger.debug(f"Broadcasted to {success_count}/{len(client_ids)} subscribers of {symbol}")
        return success_count

    async def broadcast_update(self, message: dict[str, Any]) -> int:
        """Broadcast message to all connected clients"""
        if not self.connections:
            return 0

        client_ids = list(self.connections.keys())
        success_count = 0

        # Send to all clients concurrently
        tasks = []
        for client_id in client_ids:
            task = asyncio.create_task(self.send_to_client(client_id, message))
            tasks.append(task)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count = sum(1 for result in results if result is True)

        logger.debug(f"Broadcasted to {success_count}/{len(client_ids)} connected clients")
        return success_count

    async def send_market_data_update(self, symbol: str, data: dict[str, Any]) -> None:
        """Send market data update to subscribed clients"""
        message = {
            "type": "market_data_update",
            "symbol": symbol,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }

        await self.broadcast_to_symbol_subscribers(symbol, message)

    async def send_strategy_update(self, strategy_id: str, data: dict[str, Any]) -> None:
        """Send strategy performance update to all clients"""
        message = {
            "type": "strategy_update",
            "strategy_id": strategy_id,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }

        await self.broadcast_update(message)

    async def send_risk_alert(self, alert: dict[str, Any]) -> None:
        """Send risk alert to all clients"""
        message = {"type": "risk_alert", "alert": alert, "timestamp": datetime.now().isoformat()}

        await self.broadcast_update(message)

    async def send_system_alert(self, alert: dict[str, Any]) -> None:
        """Send system alert to all clients"""
        message = {"type": "system_alert", "alert": alert, "timestamp": datetime.now().isoformat()}

        await self.broadcast_update(message)

    async def send_dashboard_update(self, payload: dict[str, Any]) -> int:
        """Broadcast a full live-analytics snapshot to all connected clients."""
        message = {
            "type": "dashboard_update",
            "data": payload,
            "timestamp": datetime.now().isoformat(),
        }
        return await self.broadcast_update(message)

    async def send_vol_surface_update(self, underlying: str, data: dict[str, Any]) -> int:
        """Broadcast a vol-surface update to subscribers of ``underlying``."""
        message = {
            "type": "vol_surface_update",
            "underlying": underlying,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        return await self.broadcast_to_symbol_subscribers(underlying, message)

    def has_active_connections(self) -> bool:
        """Check if there are any active connections"""
        return len(self.connections) > 0

    def connection_count(self) -> int:
        """Get current number of active connections"""
        return len(self.connections)

    def get_client_subscriptions(self, client_id: str) -> set[str]:
        """Get symbols subscribed by a client"""
        if client_id in self.connections:
            return self.connections[client_id].subscriptions.copy()
        return set()

    def get_symbol_subscriber_count(self, symbol: str) -> int:
        """Get number of clients subscribed to a symbol"""
        return len(self.symbol_subscriptions.get(symbol, set()))

    def get_stats(self) -> dict[str, Any]:
        """Get connection and messaging statistics"""
        active_subscriptions = sum(len(subs) for subs in self.symbol_subscriptions.values())

        return {
            **self._stats,
            "active_subscriptions": active_subscriptions,
            "subscribed_symbols": len(self.symbol_subscriptions),
            "avg_subscriptions_per_client": (
                active_subscriptions / len(self.connections) if self.connections else 0
            ),
        }

    async def _periodic_cleanup(self) -> None:
        """Periodic task to clean up inactive connections"""
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds

                inactive_clients = []
                for client_id, connection in self.connections.items():
                    if not connection.is_active:
                        inactive_clients.append(client_id)

                # Clean up inactive connections
                for client_id in inactive_clients:
                    logger.info(f"Cleaning up inactive connection: {client_id}")
                    self.disconnect(self.connections[client_id].websocket, client_id)

                # Log stats periodically
                if self.connections:
                    stats = self.get_stats()
                    logger.debug(f"WebSocket stats: {stats}")

            except Exception as e:
                logger.error(f"Error in periodic cleanup: {e}")
                await asyncio.sleep(60)  # Wait longer on error


class MarketDataBroadcaster:
    """
    Specialized broadcaster for market data updates.
    Integrates with existing market data processing pipeline.
    """

    def __init__(self, websocket_manager: WebSocketManager):
        self.websocket_manager = websocket_manager
        self.last_prices = {}
        self.update_queue = asyncio.Queue()

    async def start_broadcasting(self) -> None:
        """Start the market data broadcasting loop"""
        asyncio.create_task(self._broadcast_loop())

    async def _broadcast_loop(self) -> None:
        """Main broadcasting loop"""
        while True:
            try:
                # Get update from queue (with timeout to prevent blocking)
                try:
                    update = await asyncio.wait_for(self.update_queue.get(), timeout=1.0)
                    await self._process_market_update(update)
                except TimeoutError:
                    # No updates in queue, continue
                    continue

            except Exception as e:
                logger.error(f"Error in broadcast loop: {e}")
                await asyncio.sleep(1)

    async def _process_market_update(self, update: dict[str, Any]) -> None:
        """Process and broadcast market data update"""
        try:
            symbol = update.get("symbol")
            if not symbol:
                return

            # Only broadcast if price has changed significantly
            current_price = update.get("price", 0)
            last_price = self.last_prices.get(symbol, 0)

            price_change_threshold = 0.01  # 1% change
            if abs(current_price - last_price) / max(last_price, 1) > price_change_threshold:
                await self.websocket_manager.send_market_data_update(symbol, update)
                self.last_prices[symbol] = current_price

        except Exception as e:
            logger.error(f"Failed to process market update: {e}")

    async def queue_market_update(self, symbol: str, data: dict[str, Any]) -> None:
        """Queue a market data update for broadcasting"""
        try:
            update = {"symbol": symbol, **data}
            await self.update_queue.put(update)
        except Exception as e:
            logger.error(f"Failed to queue market update: {e}")
