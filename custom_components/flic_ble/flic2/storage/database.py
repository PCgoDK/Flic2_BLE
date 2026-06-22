"""SQLite storage for Flic 2 pairing credentials."""

import sqlite3
import logging
from pathlib import Path
from typing import Optional, List

from ..models import PairingCredentials
from ..exceptions import StorageError


_LOGGER = logging.getLogger(__name__)


class CredentialStorage:
    """SQLite storage for pairing credentials."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize credential storage.

        Args:
            db_path: Path to SQLite database file.
                     If None, uses ~/.flic2/credentials.db
        """
        if db_path is None:
            db_dir = Path.home() / ".flic2"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "credentials.db")

        self._db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS credentials (
                        address TEXT PRIMARY KEY,
                        pairing_id BLOB NOT NULL,
                        pairing_key BLOB NOT NULL,
                        button_uuid TEXT,
                        name TEXT,
                        serial_number TEXT,
                        firmware_version INTEGER,
                        last_boot_id INTEGER,
                        last_event_count INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
        except sqlite3.Error as e:
            raise StorageError(f"Failed to initialize database: {e}")

    def save(self, credentials: PairingCredentials):
        """
        Save or update credentials.

        Args:
            credentials: Credentials to save
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO credentials
                    (address, pairing_id, pairing_key, button_uuid, name,
                     serial_number, firmware_version, last_boot_id,
                     last_event_count, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    credentials.address.upper(),
                    credentials.pairing_id,
                    credentials.pairing_key,
                    credentials.button_uuid,
                    credentials.name,
                    credentials.serial_number,
                    credentials.firmware_version,
                    credentials.last_boot_id,
                    credentials.last_event_count,
                ))
                conn.commit()
                _LOGGER.debug(f"Saved credentials for {credentials.address}")
        except sqlite3.Error as e:
            raise StorageError(f"Failed to save credentials: {e}")

    def load(self, address: str) -> Optional[PairingCredentials]:
        """
        Load credentials for an address.

        Args:
            address: Bluetooth address

        Returns:
            PairingCredentials if found, None otherwise
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM credentials WHERE address = ?
                """, (address.upper(),))
                row = cursor.fetchone()

                if row is None:
                    return None

                return PairingCredentials(
                    address=row["address"],
                    pairing_id=row["pairing_id"],
                    pairing_key=row["pairing_key"],
                    button_uuid=row["button_uuid"] or "",
                    name=row["name"] or "Flic 2",
                    serial_number=row["serial_number"] or "",
                    firmware_version=row["firmware_version"] or 0,
                    last_boot_id=row["last_boot_id"],
                    last_event_count=row["last_event_count"],
                )
        except sqlite3.Error as e:
            raise StorageError(f"Failed to load credentials: {e}")

    def delete(self, address: str) -> bool:
        """
        Delete credentials for an address.

        Args:
            address: Bluetooth address

        Returns:
            True if credentials were deleted
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute("""
                    DELETE FROM credentials WHERE address = ?
                """, (address.upper(),))
                conn.commit()
                deleted = cursor.rowcount > 0
                if deleted:
                    _LOGGER.debug(f"Deleted credentials for {address}")
                return deleted
        except sqlite3.Error as e:
            raise StorageError(f"Failed to delete credentials: {e}")

    def list_all(self) -> List[PairingCredentials]:
        """
        List all stored credentials.

        Returns:
            List of all stored credentials
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM credentials ORDER BY updated_at DESC
                """)
                rows = cursor.fetchall()

                return [
                    PairingCredentials(
                        address=row["address"],
                        pairing_id=row["pairing_id"],
                        pairing_key=row["pairing_key"],
                        button_uuid=row["button_uuid"] or "",
                        name=row["name"] or "Flic 2",
                        serial_number=row["serial_number"] or "",
                        firmware_version=row["firmware_version"] or 0,
                        last_boot_id=row["last_boot_id"],
                        last_event_count=row["last_event_count"],
                    )
                    for row in rows
                ]
        except sqlite3.Error as e:
            raise StorageError(f"Failed to list credentials: {e}")

    def update_event_tracking(
        self,
        address: str,
        boot_id: Optional[int] = None,
        event_count: Optional[int] = None,
    ):
        """
        Update event tracking fields.

        Args:
            address: Bluetooth address
            boot_id: New boot ID
            event_count: New event count
        """
        try:
            updates = []
            params = []

            if boot_id is not None:
                updates.append("last_boot_id = ?")
                params.append(boot_id)

            if event_count is not None:
                updates.append("last_event_count = ?")
                params.append(event_count)

            if not updates:
                return

            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(address.upper())

            with sqlite3.connect(self._db_path) as conn:
                conn.execute(f"""
                    UPDATE credentials
                    SET {", ".join(updates)}
                    WHERE address = ?
                """, params)
                conn.commit()
        except sqlite3.Error as e:
            raise StorageError(f"Failed to update event tracking: {e}")

    def exists(self, address: str) -> bool:
        """
        Check if credentials exist for an address.

        Args:
            address: Bluetooth address

        Returns:
            True if credentials exist
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute("""
                    SELECT 1 FROM credentials WHERE address = ?
                """, (address.upper(),))
                return cursor.fetchone() is not None
        except sqlite3.Error as e:
            raise StorageError(f"Failed to check credentials: {e}")
