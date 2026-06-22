#!/usr/bin/env python3
"""
Flic 2 Button Demo

This script demonstrates:
1. Scanning for Flic 2 buttons
2. Pairing with a button (full verify)
3. Reconnecting to a paired button (quick verify)
4. Receiving and decoding button events

Usage:
    # First time - pair with button (hold button 8 sec until rapid blinking)
    python flic2_demo.py --pair

    # Subsequent times - reconnect and listen for events
    python flic2_demo.py --address <ADDRESS> --pairing-id <ID> --pairing-key <KEY>
"""

import argparse
import asyncio
import hashlib
import hmac
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bleak import BleakClient, BleakScanner

from flic2.const import FLIC2_SERVICE_UUID, FLIC2_WRITE_UUID, FLIC2_NOTIFY_UUID
from flic2.crypto import ChaskeyLTS, generate_keypair, compute_shared_secret, verify_button_identity
from flic2.crypto.keys import generate_random
from flic2.protocol.packets import PacketEncoder, PacketDecoder
from flic2.protocol.opcodes import Opcode
from flic2.models import ButtonEventType


class Flic2Demo:
    """Flic 2 button demo client."""

    # Opcodes for button communication
    QUICK_VERIFY_REQUEST = 0x05
    QUICK_VERIFY_RESPONSE = 0x08
    INIT_BUTTON_EVENTS_LIGHT = 0x17
    INIT_BUTTON_EVENTS_RESPONSE = 0x0A
    INIT_BUTTON_EVENTS_RESPONSE_NO_BOOT = 0x0B
    BUTTON_EVENT_NOTIFICATION = 0x0C
    DISCONNECTED_LINK = 0x09

    def __init__(self, address: str):
        self.address = address
        self.client = None
        self.rx_event = asyncio.Event()
        self.rx_data = bytearray()

        # Session state
        self.conn_id = 0
        self.session_key = None
        self.chaskey = None
        self.tx_counter = 0
        self.rx_counter = 0

    def _on_notify(self, sender, data: bytes):
        """Handle BLE notifications."""
        self.rx_data.clear()
        self.rx_data.extend(data)
        self.rx_event.set()

    async def connect(self) -> bool:
        """Connect to the button."""
        print(f"Connecting to {self.address}...")
        self.client = BleakClient(self.address, timeout=20.0)
        await self.client.connect()
        await self.client.start_notify(FLIC2_NOTIFY_UUID, self._on_notify)
        print("Connected!")
        return True

    async def disconnect(self):
        """Disconnect from the button."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()

    async def pair(self) -> dict:
        """
        Perform full verify pairing.

        Returns credentials dict with pairing_id, pairing_key, session_key.
        """
        enc = PacketEncoder()
        dec = PacketDecoder()

        # Generate our keypair and random values
        priv, pub = generate_keypair()
        tmp_id = generate_random(4)
        client_random = generate_random(8)

        # Step 1: Send FullVerifyRequest1
        req1 = enc.encode_full_verify_request_1(tmp_id)
        print(f"Sending FullVerifyRequest1...")
        await self.client.write_gatt_char(FLIC2_WRITE_UUID, req1, response=False)

        await asyncio.wait_for(self.rx_event.wait(), timeout=10)
        self.rx_event.clear()

        pkt = dec.decode(bytes(self.rx_data))
        if pkt.opcode != Opcode.FULL_VERIFY_RESPONSE_1:
            raise Exception(f"Unexpected opcode: {pkt.opcode:#04x}")

        # Parse response
        payload = pkt.payload
        flags = payload[115] if len(payload) >= 116 else 0
        is_public_mode = (flags >> 1) & 1

        if not is_public_mode:
            raise Exception("Button not in pairing mode! Hold for 8 seconds until rapid blinking.")

        # Extract button data
        offset = 4  # Skip tmp_id echo
        signature = bytes(payload[offset:offset + 64]); offset += 64
        button_addr = bytes(payload[offset:offset + 6]); offset += 6
        addr_type = payload[offset]; offset += 1
        button_pubkey = bytes(payload[offset:offset + 32]); offset += 32
        button_random = bytes(payload[offset:offset + 8])

        print(f"Button address: {button_addr.hex()}")

        # Verify Ed25519 signature
        sig_bits = verify_button_identity(signature, button_addr, addr_type, button_pubkey)
        print(f"Ed25519 verified (sig_bits={sig_bits})")

        # Compute shared secret via X25519
        shared_secret = compute_shared_secret(priv, button_pubkey)

        # Derive keys
        fvs_input = shared_secret + bytes([sig_bits]) + button_random + client_random + bytes([0])
        full_verify_secret = hashlib.sha256(fvs_input).digest()

        verifier = hmac.new(full_verify_secret, b"AT", hashlib.sha256).digest()[:16]
        session_key = hmac.new(full_verify_secret, b"SK", hashlib.sha256).digest()[:16]
        pairing_data = hmac.new(full_verify_secret, b"PK", hashlib.sha256).digest()[:20]
        pairing_id = pairing_data[:4]
        pairing_key = pairing_data[4:20]

        self.conn_id = pkt.conn_id

        # Step 2: Send FullVerifyRequest2
        req2 = enc.encode_full_verify_request_2(pub, client_random, verifier, self.conn_id)
        print(f"Sending FullVerifyRequest2...")
        await self.client.write_gatt_char(FLIC2_WRITE_UUID, req2, response=False)

        await asyncio.wait_for(self.rx_event.wait(), timeout=15)
        self.rx_event.clear()

        pkt2 = dec.decode(bytes(self.rx_data))
        if pkt2.opcode == Opcode.FULL_VERIFY_FAIL_RESPONSE_2:
            reason = pkt2.payload[0] if pkt2.payload else 0
            reasons = {0: "INVALID_VERIFIER", 1: "NOT_IN_PUBLIC_MODE"}
            raise Exception(f"Pairing failed: {reasons.get(reason, reason)}")

        if pkt2.opcode != Opcode.FULL_VERIFY_RESPONSE_2:
            raise Exception(f"Unexpected opcode: {pkt2.opcode:#04x}")

        print("Pairing successful!")

        # Set session state
        self.session_key = session_key
        self.chaskey = ChaskeyLTS(session_key)
        self.tx_counter = 0
        self.rx_counter = 0

        return {
            "pairing_id": pairing_id.hex(),
            "pairing_key": pairing_key.hex(),
            "session_key": session_key.hex(),
        }

    async def quick_verify(self, pairing_id: bytes, pairing_key: bytes) -> bool:
        """
        Perform quick verify (reconnection with existing credentials).

        Args:
            pairing_id: 4-byte pairing ID from initial pairing
            pairing_key: 16-byte pairing key from initial pairing

        Returns:
            True if successful
        """
        client_random = generate_random(8)
        tmp_id = generate_random(4)

        # Build QuickVerifyRequest
        flags = 0x00
        payload = client_random[:7] + bytes([flags]) + tmp_id + pairing_id

        header = 0x00  # conn_id 0 for initial request
        packet = bytes([header, self.QUICK_VERIFY_REQUEST]) + payload

        print("Sending QuickVerifyRequest...")
        await self.client.write_gatt_char(FLIC2_WRITE_UUID, packet, response=False)

        await asyncio.wait_for(self.rx_event.wait(), timeout=10)
        self.rx_event.clear()

        data = bytes(self.rx_data)
        header_byte = data[0]
        opcode = data[1]

        self.conn_id = header_byte & 0x1F

        if opcode != self.QUICK_VERIFY_RESPONSE:
            raise Exception(f"Quick verify failed, opcode: {opcode:#04x}")

        # Extract button random from response (skip header, opcode)
        button_random = data[2:10]

        # Derive session key
        seed = client_random[:7] + bytes([0x00]) + button_random[:8]
        chaskey_pairing = ChaskeyLTS(pairing_key)
        self.session_key = chaskey_pairing.encrypt_block(seed)
        self.chaskey = ChaskeyLTS(self.session_key)
        self.tx_counter = 0
        self.rx_counter = 0

        print(f"Quick verify successful! (conn_id={self.conn_id})")
        return True

    async def init_button_events(self) -> bool:
        """
        Initialize button event reception.

        Must be called after pairing or quick verify.
        """
        # Build InitButtonEventsLightRequest
        event_count = 0
        boot_id = 0
        auto_disconnect_time = 511
        max_queued_packets = 31
        max_queued_packets_age = 0xFFFFF
        enable_hid = 0

        # Pack bit fields
        bitfield_val = (
            auto_disconnect_time |
            (max_queued_packets << 9) |
            (max_queued_packets_age << 14) |
            (enable_hid << 34)
        )
        bitfield_bytes = bitfield_val.to_bytes(5, 'little')

        payload = (
            event_count.to_bytes(4, 'little') +
            boot_id.to_bytes(4, 'little') +
            bitfield_bytes
        )

        # Build and sign packet (signature excludes header byte)
        packet_body = bytes([self.INIT_BUTTON_EVENTS_LIGHT]) + payload
        signature = self.chaskey.mac_with_dir_and_counter(packet_body, 1, self.tx_counter)
        self.tx_counter += 1

        packet = bytes([self.conn_id & 0x1F]) + packet_body + signature

        print("Initializing button events...")
        await self.client.write_gatt_char(FLIC2_WRITE_UUID, packet, response=False)

        await asyncio.wait_for(self.rx_event.wait(), timeout=10)
        self.rx_event.clear()

        data = bytes(self.rx_data)
        opcode = data[1]

        if opcode == self.DISCONNECTED_LINK:
            reason = data[2] if len(data) > 2 else 0
            reasons = {0: "PING_TIMEOUT", 1: "INVALID_SIGNATURE", 2: "NEW_CONNECTION", 3: "BY_USER"}
            raise Exception(f"Disconnected: {reasons.get(reason, reason)}")

        if opcode in (self.INIT_BUTTON_EVENTS_RESPONSE, self.INIT_BUTTON_EVENTS_RESPONSE_NO_BOOT):
            print("Button events initialized!")
            return True

        raise Exception(f"Unexpected response: {opcode:#04x}")

    def decode_button_events(self, payload: bytes) -> list:
        """
        Decode button event notification payload.

        Args:
            payload: Raw payload (excluding opcode)

        Returns:
            List of (event_type_name, was_queued, age_seconds) tuples
        """
        events = []

        if len(payload) < 4:
            return events

        press_counter = int.from_bytes(payload[0:4], 'little')

        # Each event is 7 bytes: timestamp(6) + event_info(1)
        offset = 4
        while offset + 7 <= len(payload):
            timestamp = int.from_bytes(payload[offset:offset + 6], 'little')
            event_info = payload[offset + 6]

            event_encoded = event_info & 0x0F
            was_queued = bool((event_info >> 4) & 0x01)
            was_queued_last = bool((event_info >> 5) & 0x01)

            # Decode event type based on encoding
            if (event_encoded >> 3) != 0:
                # Button up with additional info
                event_type = "UP"
                if event_encoded & 0x04:
                    event_type = "HOLD"
                elif event_encoded & 0x02:
                    if event_encoded & 0x01:
                        event_type = "DOUBLE_CLICK"
                    else:
                        event_type = "SINGLE_CLICK"
            else:
                # Simple event
                event_types = {0: "UP", 1: "DOWN", 2: "CLICK_TIMEOUT", 7: "DOUBLE_CLICK_PENDING"}
                event_type = event_types.get(event_encoded, f"UNKNOWN({event_encoded})")

            # Calculate age if queued (timestamp is in 32768 Hz ticks)
            age_seconds = 0.0  # Would need init_timestamp to calculate properly

            events.append((event_type, was_queued, press_counter))
            offset += 7

        return events

    async def listen_for_events(self, duration: float = 60.0):
        """
        Listen for button events.

        Args:
            duration: How long to listen (seconds)
        """
        print(f"\nListening for button events for {duration} seconds...")
        print("Press the button!")
        print("-" * 50)

        end_time = asyncio.get_event_loop().time() + duration

        while asyncio.get_event_loop().time() < end_time:
            try:
                remaining = end_time - asyncio.get_event_loop().time()
                await asyncio.wait_for(self.rx_event.wait(), timeout=min(remaining, 5.0))
                self.rx_event.clear()

                data = bytes(self.rx_data)
                opcode = data[1]

                if opcode == self.BUTTON_EVENT_NOTIFICATION:
                    # Payload starts after header(1) + opcode(1), ends before signature(5)
                    payload = data[2:-5]
                    events = self.decode_button_events(payload)
                    for event_type, was_queued, counter in events:
                        queued_str = " (queued)" if was_queued else ""
                        print(f"  {event_type}{queued_str} [counter={counter}]")
                elif opcode == self.DISCONNECTED_LINK:
                    print("Button disconnected")
                    break

            except asyncio.TimeoutError:
                continue

        print("-" * 50)
        print("Done listening.")


async def scan_for_flic():
    """Scan for Flic 2 buttons."""
    print("Scanning for Flic 2 buttons...")
    devices = await BleakScanner.discover(timeout=10.0, service_uuids=[FLIC2_SERVICE_UUID])
    for d in devices:
        if d.name and "Flic" in d.name:
            print(f"  Found: {d.name} ({d.address})")
            return d.address
    return None


async def main():
    parser = argparse.ArgumentParser(description="Flic 2 Button Demo")
    parser.add_argument("--pair", action="store_true", help="Pair with a new button")
    parser.add_argument("--address", help="Button BLE address")
    parser.add_argument("--pairing-id", help="Pairing ID (hex)")
    parser.add_argument("--pairing-key", help="Pairing key (hex)")
    args = parser.parse_args()

    if args.pair:
        # Pairing mode
        address = args.address or await scan_for_flic()
        if not address:
            print("No Flic button found! Click the button to wake it.")
            return

        print("\n" + "=" * 60)
        print("PAIRING MODE")
        print("=" * 60)
        print("1. Hold the button for 8 seconds until LED blinks RAPIDLY")
        print("2. Release the button")
        print("3. Wait for pairing to complete")
        print("=" * 60 + "\n")

        demo = Flic2Demo(address)
        try:
            await demo.connect()
            creds = await demo.pair()

            print("\n" + "=" * 60)
            print("PAIRING SUCCESSFUL!")
            print("=" * 60)
            print(f"Address:     {address}")
            print(f"Pairing ID:  {creds['pairing_id']}")
            print(f"Pairing Key: {creds['pairing_key']}")
            print("\nTo reconnect, run:")
            print(f"  python flic2_demo.py --address {address} \\")
            print(f"    --pairing-id {creds['pairing_id']} \\")
            print(f"    --pairing-key {creds['pairing_key']}")
            print("=" * 60)

            # Initialize and listen for events
            await demo.init_button_events()
            await demo.listen_for_events(30)

        finally:
            await demo.disconnect()

    elif args.address and args.pairing_id and args.pairing_key:
        # Reconnect mode
        demo = Flic2Demo(args.address)
        try:
            await demo.connect()
            await demo.quick_verify(
                bytes.fromhex(args.pairing_id),
                bytes.fromhex(args.pairing_key)
            )
            await demo.init_button_events()
            await demo.listen_for_events(60)
        finally:
            await demo.disconnect()

    else:
        parser.print_help()
        print("\nExamples:")
        print("  # Pair with a new button:")
        print("  python flic2_demo.py --pair")
        print()
        print("  # Reconnect to a paired button:")
        print("  python flic2_demo.py --address XX:XX:XX:XX:XX:XX --pairing-id XXXX --pairing-key XXXX")


if __name__ == "__main__":
    asyncio.run(main())
