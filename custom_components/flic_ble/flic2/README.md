# Flic 2 BLE Protocol Library

A Python library for communicating with Flic 2 buttons via Bluetooth Low Energy (BLE).

## Features

- **Full Pairing** - Pair with new Flic 2 buttons using Ed25519/X25519 cryptography
- **Quick Verify** - Reconnect to previously paired buttons efficiently
- **Button Events** - Receive click, double-click, and hold events
- **Queued Events** - Retrieve events that occurred while disconnected

## Installation

```bash
pip install bleak cryptography
```

## Quick Start

### Pairing with a New Button

1. Hold the Flic button for 8 seconds until the LED blinks rapidly
2. Run the pairing script:

```python
import asyncio
from flic2.examples.flic2_demo import Flic2Demo, scan_for_flic

async def pair():
    address = await scan_for_flic()
    demo = Flic2Demo(address)
    await demo.connect()
    credentials = await demo.pair()
    print(f"Pairing ID: {credentials['pairing_id']}")
    print(f"Pairing Key: {credentials['pairing_key']}")

asyncio.run(pair())
```

### Reconnecting to a Paired Button

```python
import asyncio
from flic2.examples.flic2_demo import Flic2Demo

async def reconnect():
    demo = Flic2Demo("XX:XX:XX:XX:XX:XX")
    await demo.connect()
    await demo.quick_verify(
        bytes.fromhex("your_pairing_id"),
        bytes.fromhex("your_pairing_key")
    )
    await demo.init_button_events()
    await demo.listen_for_events(60)

asyncio.run(reconnect())
```

## Protocol Overview

### Connection Flow

1. **Scan** - Discover Flic buttons via BLE advertising (Service UUID: `00420000-8f59-4420-870d-84f3b617e493`)
2. **Connect** - Establish BLE connection
3. **Authenticate**:
   - **Full Verify** (first time): Exchange keys, verify button identity, establish session
   - **Quick Verify** (reconnect): Use stored credentials to establish session
4. **Initialize Events** - Tell button we're ready to receive events
5. **Listen** - Receive button event notifications

### Packet Format

```
┌─────────┬────────┬─────────┬───────────┐
│ Header  │ Opcode │ Payload │ Signature │
│ (1 byte)│(1 byte)│ (var)   │ (5 bytes) │
└─────────┴────────┴─────────┴───────────┘
```

**Header byte:**
- Bits 0-4: Connection ID
- Bit 5: Newly assigned flag
- Bit 6: Multi flag (reserved)
- Bit 7: Fragment flag

**Signature:** Chaskey-LTS MAC (only after session established)

### Cryptography

- **Ed25519** - Verify button identity using Flic's public key
- **X25519** - ECDH key exchange for shared secret
- **SHA-256** - Key derivation
- **HMAC-SHA256** - Derive verifier, session key, pairing credentials
- **Chaskey-LTS** - 16-round MAC for packet authentication

### Button Event Types

| Type | Description |
|------|-------------|
| `UP` | Button released |
| `DOWN` | Button pressed |
| `CLICK` | Press and release within 1 second |
| `SINGLE_CLICK` | Single click (no double-click followed) |
| `DOUBLE_CLICK` | Two clicks within 0.5 seconds |
| `HOLD` | Button held for 1+ seconds |

## Module Structure

```
flic2/
├── __init__.py          # Package exports
├── const.py             # BLE UUIDs, protocol constants
├── models.py            # Data classes (ButtonEvent, etc.)
├── exceptions.py        # Custom exceptions
├── crypto/
│   ├── chaskey_lts.py   # Chaskey-LTS MAC implementation
│   ├── ed25519.py       # Ed25519 signature verification
│   └── keys.py          # X25519 ECDH, key derivation
├── protocol/
│   ├── opcodes.py       # Protocol opcodes
│   ├── packets.py       # Packet encode/decode
│   └── state_machine.py # Pairing state machine
├── connection/
│   ├── client.py        # Main Flic2Client
│   └── scanner.py       # BLE discovery
└── examples/
    └── flic2_demo.py    # Demo script
```

## Key Implementation Notes

### Chaskey-LTS Differences

The Flic protocol uses a **modified Chaskey-LTS** implementation:

1. **Subkey generation** shifts bits from MSB to LSB (opposite of standard)
2. **Permutation** uses different rotations than standard Chaskey-LTS
3. **Signed packets** use direction (TX=1, RX=0) and counter in MAC computation

### Header Byte Format

The header byte format differs from some documentation:
- Connection ID is in bits **0-4** (not 3-7)
- Signature computation **excludes** the header byte

### Session Key Derivation (Quick Verify)

```
seed = client_random[0:7] || 0x00 || button_random[0:8]
session_key = Chaskey_encrypt(pairing_key, seed)
```

## BLE Characteristics

| Name | UUID | Properties |
|------|------|------------|
| Service | `00420000-8f59-4420-870d-84f3b617e493` | - |
| Write | `00420001-8f59-4420-870d-84f3b617e493` | Write Without Response |
| Notify | `00420002-8f59-4420-870d-84f3b617e493` | Notify |

## References

- [flic2lib-c-module](https://github.com/50ButtonsEach/flic2lib-c-module) - Official C implementation
- [Flic Developer Portal](https://flic.io/developers)

## License

This library is provided for educational and personal use. Flic is a trademark of Shortcut Labs AB.
