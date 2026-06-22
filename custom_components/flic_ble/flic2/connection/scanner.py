"""BLE scanner for discovering Flic 2 buttons."""

import asyncio
import logging
from typing import List, Optional, Dict, Callable

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from ..const import FLIC2_SERVICE_UUID, DEFAULT_SCAN_TIMEOUT


_LOGGER = logging.getLogger(__name__)


class Flic2Scanner:
    """Scanner for discovering Flic 2 buttons."""

    def __init__(self):
        self._discovered: Dict[str, BLEDevice] = {}
        self._on_discovered: Optional[Callable[[BLEDevice, AdvertisementData], None]] = None

    def _detection_callback(
        self,
        device: BLEDevice,
        advertisement_data: AdvertisementData,
    ):
        """Handle discovered device."""
        # Check if this is a Flic 2 button
        if FLIC2_SERVICE_UUID.lower() in [
            uuid.lower() for uuid in (advertisement_data.service_uuids or [])
        ]:
            if device.address not in self._discovered:
                _LOGGER.debug(
                    f"Discovered Flic 2: {device.name or 'Unknown'} ({device.address})"
                )
                self._discovered[device.address] = device

                if self._on_discovered:
                    self._on_discovered(device, advertisement_data)

    async def scan(
        self,
        timeout: float = DEFAULT_SCAN_TIMEOUT,
        on_discovered: Optional[Callable[[BLEDevice, AdvertisementData], None]] = None,
    ) -> List[BLEDevice]:
        """
        Scan for Flic 2 buttons.

        Args:
            timeout: Scan duration in seconds
            on_discovered: Optional callback for each discovered device

        Returns:
            List of discovered Flic 2 devices
        """
        self._discovered.clear()
        self._on_discovered = on_discovered

        _LOGGER.info(f"Scanning for Flic 2 buttons for {timeout}s...")

        scanner = BleakScanner(
            detection_callback=self._detection_callback,
            service_uuids=[FLIC2_SERVICE_UUID],
        )

        try:
            await scanner.start()
            await asyncio.sleep(timeout)
            await scanner.stop()
        except Exception as e:
            _LOGGER.error(f"Scan error: {e}")
            raise

        devices = list(self._discovered.values())
        _LOGGER.info(f"Found {len(devices)} Flic 2 button(s)")

        return devices

    async def find_by_address(
        self,
        address: str,
        timeout: float = DEFAULT_SCAN_TIMEOUT,
    ) -> Optional[BLEDevice]:
        """
        Find a specific Flic 2 button by address.

        Args:
            address: Bluetooth address to find
            timeout: Scan duration in seconds

        Returns:
            BLEDevice if found, None otherwise
        """
        target_address = address.upper()
        found_device: Optional[BLEDevice] = None
        found_event = asyncio.Event()

        def on_found(device: BLEDevice, _: AdvertisementData):
            nonlocal found_device
            if device.address.upper() == target_address:
                found_device = device
                found_event.set()

        self._discovered.clear()
        self._on_discovered = on_found

        _LOGGER.debug(f"Searching for Flic 2 at {address}...")

        scanner = BleakScanner(
            detection_callback=self._detection_callback,
            service_uuids=[FLIC2_SERVICE_UUID],
        )

        try:
            await scanner.start()

            # Wait for device or timeout
            try:
                await asyncio.wait_for(found_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

            await scanner.stop()
        except Exception as e:
            _LOGGER.error(f"Scan error: {e}")
            raise

        return found_device


async def discover_flic2_buttons(
    timeout: float = DEFAULT_SCAN_TIMEOUT,
) -> List[BLEDevice]:
    """
    Convenience function to discover Flic 2 buttons.

    Args:
        timeout: Scan duration in seconds

    Returns:
        List of discovered Flic 2 devices
    """
    scanner = Flic2Scanner()
    return await scanner.scan(timeout=timeout)
