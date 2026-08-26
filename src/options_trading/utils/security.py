# src/options_trading/utils/security.py
"""Enhanced secure storage utilities for token management."""

import base64
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import aiofiles
import keyring
from cryptography.fernet import Fernet

from ..config.settings import get_settings
from ..models.auth import TokenInfo

logger = logging.getLogger(__name__)


class SecureStorage:
    """Enhanced secure storage for authentication tokens with multiple storage backends."""

    def __init__(self):
        self.settings = get_settings()
        self.storage_dir = Path(self.settings.token_storage_dir) / ".trading_platform"
        self.storage_dir.mkdir(exist_ok=True, parents=True)
        self.token_file = self.storage_dir / "tokens.json"
        self._encryption_key = None

    def _get_encryption_key(self) -> bytes:
        """Get or create encryption key for secure storage."""
        if self._encryption_key:
            return self._encryption_key

        try:
            # Try to get existing key from keyring
            key_str = keyring.get_password(self.settings.token_keyring_service, "encryption_key")

            if key_str:
                self._encryption_key = base64.urlsafe_b64decode(key_str.encode())
            else:
                # Generate new key
                self._encryption_key = Fernet.generate_key()
                # Store in keyring
                keyring.set_password(
                    self.settings.token_keyring_service,
                    "encryption_key",
                    base64.urlsafe_b64encode(self._encryption_key).decode(),
                )
                logger.info("Generated new encryption key for token storage")

        except Exception as e:
            logger.warning(f"Keyring access failed, generating ephemeral key: {e}")
            import os

            self._encryption_key = os.urandom(32)

        return self._encryption_key

    def _encrypt_data(self, data: str) -> str:
        """Encrypt sensitive data."""
        try:
            fernet = Fernet(base64.urlsafe_b64encode(self._get_encryption_key()))
            encrypted = fernet.encrypt(data.encode())
            return base64.urlsafe_b64encode(encrypted).decode()
        except Exception as e:
            raise RuntimeError(f"Token encryption failed — refusing to store plaintext: {e}") from e

    def _decrypt_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data."""
        try:
            fernet = Fernet(base64.urlsafe_b64encode(self._get_encryption_key()))
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted = fernet.decrypt(encrypted_bytes)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            # Try base64 decode as fallback
            try:
                return base64.urlsafe_b64decode(encrypted_data.encode()).decode()
            except Exception:
                raise ValueError("Unable to decrypt token data")

    async def _load_token_file(self) -> dict[str, dict]:
        """Load token file with error handling."""
        if not self.token_file.exists():
            return {}

        try:
            async with aiofiles.open(self.token_file) as f:
                content = await f.read()
                if not content.strip():
                    return {}
                return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Token file corrupted: {e}")
            # Backup corrupted file
            backup_file = self.token_file.with_suffix(".backup")
            try:
                self.token_file.rename(backup_file)
                logger.info(f"Corrupted token file backed up to: {backup_file}")
            except Exception:
                pass
            return {}
        except Exception as e:
            logger.error(f"Error loading token file: {e}")
            return {}

    async def _save_token_file(self, data: dict[str, dict]) -> None:
        """Save token file with atomic write."""
        temp_file = self.token_file.with_suffix(".tmp")

        try:
            async with aiofiles.open(temp_file, "w") as f:
                await f.write(json.dumps(data, indent=2, default=str))
                await f.flush()

            # Atomic replace
            temp_file.replace(self.token_file)

            # Set restrictive permissions (owner only)
            self.token_file.chmod(0o600)

        except Exception as e:
            logger.error(f"Error saving token file: {e}")
            # Clean up temp file
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            raise

    async def store_token(self, token_info: TokenInfo) -> None:
        """Store token securely with multiple validation checks."""
        try:
            # Validate token info
            if not token_info.access_token:
                raise ValueError("Access token cannot be empty")

            if not token_info.user_id:
                raise ValueError("User ID cannot be empty")

            # Load existing tokens
            tokens = await self._load_token_file()

            # Prepare token data for storage
            token_data = {
                "access_token": self._encrypt_data(token_info.access_token),
                "refresh_token": self._encrypt_data(token_info.refresh_token)
                if token_info.refresh_token
                else None,
                "expires_at": token_info.expires_at.isoformat(),
                "created_at": token_info.created_at.isoformat(),
                "user_id": token_info.user_id,
                "token_type": token_info.token_type,
                "stored_at": datetime.now().isoformat(),
            }

            # Store token
            tokens[token_info.user_id] = token_data

            # Save to file
            await self._save_token_file(tokens)

            logger.info(f"Token stored securely for user: {token_info.user_id}")

        except Exception as e:
            logger.error(f"Failed to store token: {e}")
            raise

    async def load_token(self, user_id: str) -> TokenInfo | None:
        """Load token securely with validation."""
        try:
            tokens = await self._load_token_file()

            if user_id not in tokens:
                return None

            token_data = tokens[user_id]

            # Decrypt sensitive data
            access_token = self._decrypt_data(token_data["access_token"])
            refresh_token = None
            if token_data.get("refresh_token"):
                refresh_token = self._decrypt_data(token_data["refresh_token"])

            # Create TokenInfo object
            token_info = TokenInfo(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=datetime.fromisoformat(token_data["expires_at"]),
                created_at=datetime.fromisoformat(token_data["created_at"]),
                user_id=token_data["user_id"],
                token_type=token_data.get("token_type", "Bearer"),
            )

            logger.debug(f"Token loaded for user: {user_id}")
            return token_info

        except Exception as e:
            logger.error(f"Failed to load token for user {user_id}: {e}")
            return None

    async def clear_token(self, user_id: str) -> bool:
        """Clear token for specific user."""
        try:
            tokens = await self._load_token_file()

            if user_id in tokens:
                del tokens[user_id]
                await self._save_token_file(tokens)
                logger.info(f"Token cleared for user: {user_id}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to clear token for user {user_id}: {e}")
            return False

    async def list_stored_users(self) -> list[str]:
        """List all users with stored tokens."""
        try:
            tokens = await self._load_token_file()
            users = list(tokens.keys())
            logger.debug(f"Found {len(users)} stored users")
            return users
        except Exception as e:
            logger.error(f"Failed to list stored users: {e}")
            return []

    async def cleanup_expired_tokens(self) -> int:
        """Remove expired tokens from storage."""
        try:
            tokens = await self._load_token_file()
            current_time = datetime.now()

            expired_users = []
            for user_id, token_data in tokens.items():
                try:
                    expires_at = datetime.fromisoformat(token_data["expires_at"])
                    # Add buffer time for cleanup
                    if expires_at < (current_time - timedelta(days=7)):
                        expired_users.append(user_id)
                except Exception:
                    # Invalid date format, mark for cleanup
                    expired_users.append(user_id)

            # Remove expired tokens
            for user_id in expired_users:
                del tokens[user_id]

            if expired_users:
                await self._save_token_file(tokens)
                logger.info(f"Cleaned up {len(expired_users)} expired tokens")

            return len(expired_users)

        except Exception as e:
            logger.error(f"Token cleanup failed: {e}")
            return 0

    async def get_storage_info(self) -> dict[str, any]:
        """Get information about token storage."""
        try:
            tokens = await self._load_token_file()
            file_size = self.token_file.stat().st_size if self.token_file.exists() else 0

            return {
                "storage_path": str(self.token_file),
                "file_size_bytes": file_size,
                "stored_users_count": len(tokens),
                "users": list(tokens.keys()),
                "last_modified": datetime.fromtimestamp(self.token_file.stat().st_mtime).isoformat()
                if self.token_file.exists()
                else None,
            }
        except Exception as e:
            logger.error(f"Failed to get storage info: {e}")
            return {"storage_path": str(self.token_file), "error": str(e)}
