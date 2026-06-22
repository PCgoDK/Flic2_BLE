"""Config flow for Flic 2 BLE integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import (
    CONF_BUTTON_UUID,
    CONF_FIRMWARE_VERSION,
    CONF_NAME,
    CONF_PAIRING_ID,
    CONF_PAIRING_KEY,
    CONF_SERIAL_NUMBER,
    CONNECTION_TIMEOUT,
    DOMAIN,
    FLIC2_SERVICE_UUID,
    PAIRING_TIMEOUT,
)
from .flic2 import Flic2Client, PairingCredentials, PairingError, TimeoutError

_LOGGER = logging.getLogger(__name__)


class FlicBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Flic 2 BLE."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._address: str | None = None
        self._name: str | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        self._address = discovery_info.address
        self._name = discovery_info.name or f"Flic 2 {discovery_info.address[-5:]}"

        self.context["title_placeholders"] = {"name": self._name}

        return await self.async_step_confirm_pair()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            discovery = self._discovered_devices[address]
            self._discovery_info = discovery
            self._address = address
            self._name = discovery.name or f"Flic 2 {address[-5:]}"
            self.context["title_placeholders"] = {"name": self._name}

            return await self.async_step_confirm_pair()

        # Discover available Flic 2 devices
        current_addresses = self._async_current_ids(include_ignore=False)
        for discovery_info in async_discovered_service_info(self.hass, connectable=True):
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue
            # Check if this is a Flic 2 device by service UUID
            service_uuids = [str(uuid).lower() for uuid in discovery_info.service_uuids]
            if FLIC2_SERVICE_UUID.lower() in service_uuids:
                self._discovered_devices[address] = discovery_info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        titles = {
            address: info.name or f"Flic 2 {address[-5:]}"
            for address, info in self._discovered_devices.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(titles)}),
        )

    async def async_step_confirm_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm and perform pairing with the button."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Perform pairing
            try:
                credentials = await self._async_pair_button()
                return self._async_create_entry_from_credentials(credentials)
            except TimeoutError:
                _LOGGER.warning("Pairing timed out for %s", self._address)
                errors["base"] = "pairing_timeout"
            except PairingError as err:
                error_msg = str(err).lower()
                _LOGGER.warning("Pairing failed for %s: %s", self._address, err)
                if "not in pairing mode" in error_msg:
                    errors["base"] = "not_in_pairing_mode"
                else:
                    errors["base"] = "pairing_failed"
            except Exception:
                _LOGGER.exception("Unexpected error during pairing")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="confirm_pair",
            description_placeholders={"name": self._name},
            errors=errors,
        )

    async def _async_pair_button(self) -> PairingCredentials:
        """Perform pairing with the Flic 2 button."""
        if not self._address:
            raise PairingError("No address configured")

        ble_device = async_ble_device_from_address(
            self.hass, self._address, connectable=True
        )
        if not ble_device:
            raise PairingError(f"Device {self._address} not found")

        client = Flic2Client()
        try:
            await client.connect(ble_device, timeout=CONNECTION_TIMEOUT)
            credentials = await client.pair(timeout=PAIRING_TIMEOUT)

            # Initialize button events to commit the pairing to button storage
            # This is required before disconnecting or the pairing won't persist
            _LOGGER.debug("Initializing button events to commit pairing")
            if not await client.init_button_events():
                _LOGGER.warning("Failed to init button events, pairing may not persist")

            return credentials
        finally:
            await client.disconnect()

    def _async_create_entry_from_credentials(
        self, credentials: PairingCredentials
    ) -> ConfigFlowResult:
        """Create config entry from pairing credentials."""
        return self.async_create_entry(
            title=credentials.name or self._name or "Flic 2",
            data={
                CONF_ADDRESS: self._address,
                CONF_NAME: credentials.name,
                CONF_PAIRING_ID: credentials.pairing_id.hex(),
                CONF_PAIRING_KEY: credentials.pairing_key.hex(),
                CONF_BUTTON_UUID: credentials.button_uuid,
                CONF_SERIAL_NUMBER: credentials.serial_number,
                CONF_FIRMWARE_VERSION: credentials.firmware_version,
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when pairing is lost."""
        self._address = entry_data[CONF_ADDRESS]
        self._name = entry_data.get(CONF_NAME) or f"Flic 2 {self._address[-5:]}"
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-pairing with the button."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                credentials = await self._async_pair_button()
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={
                        CONF_NAME: credentials.name,
                        CONF_PAIRING_ID: credentials.pairing_id.hex(),
                        CONF_PAIRING_KEY: credentials.pairing_key.hex(),
                        CONF_BUTTON_UUID: credentials.button_uuid,
                        CONF_SERIAL_NUMBER: credentials.serial_number,
                        CONF_FIRMWARE_VERSION: credentials.firmware_version,
                    },
                )
            except TimeoutError:
                _LOGGER.warning("Re-pairing timed out for %s", self._address)
                errors["base"] = "pairing_timeout"
            except PairingError as err:
                error_msg = str(err).lower()
                _LOGGER.warning("Re-pairing failed for %s: %s", self._address, err)
                if "not in pairing mode" in error_msg:
                    errors["base"] = "not_in_pairing_mode"
                else:
                    errors["base"] = "pairing_failed"
            except Exception:
                _LOGGER.exception("Unexpected error during re-pairing")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"name": self._name},
            errors=errors,
        )
