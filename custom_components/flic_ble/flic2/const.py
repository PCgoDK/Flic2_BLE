"""Constants for Flic 2 BLE protocol."""

# BLE Service and Characteristic UUIDs
FLIC2_SERVICE_UUID = "00420000-8f59-4420-870d-84f3b617e493"
FLIC2_WRITE_UUID = "00420001-8f59-4420-870d-84f3b617e493"
FLIC2_NOTIFY_UUID = "00420002-8f59-4420-870d-84f3b617e493"

# Flic public key for Ed25519 verification (hex)
FLIC_PUBLIC_KEY_HEX = "d33f2440dd54b31b2e1dcf40132efa41d8f8a7474168df4008f5a95fb3b0d022"

# Protocol constants
SIGNATURE_LENGTH = 5
MAX_PACKET_SIZE = 20
TMP_ID_LENGTH = 4
PAIRING_ID_LENGTH = 4
PAIRING_KEY_LENGTH = 16

# Header byte format (from flic2lib-c-module):
# Bits 0-4: conn_id (mask 0x1F)
# Bit 5: newly_assigned (0x20)
# Bit 6: multi (0x40) - reserved/unused
# Bit 7: fragment (0x80) - 1 means more fragments coming, 0 means last/complete
CONN_ID_MASK = 0b00011111        # 0x1F
NEWLY_ASSIGNED_BIT = 0b00100000  # 0x20
MULTI_BIT = 0b01000000           # 0x40
FRAGMENT_BIT = 0b10000000        # 0x80

# Timeouts
DEFAULT_SCAN_TIMEOUT = 10.0
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_OPERATION_TIMEOUT = 5.0
