"""Ed25519 signature verification for Flic button identity."""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from ..const import FLIC_PUBLIC_KEY_HEX


# Flic's Ed25519 public key for verifying button identities
FLIC_PUBLIC_KEY = bytes.fromhex(FLIC_PUBLIC_KEY_HEX)


def verify_button_identity(
    signature: bytes,
    address: bytes,
    address_type: int,
    ecdh_pubkey: bytes,
) -> int:
    """
    Verify button identity signature from Flic.

    The signature covers: address(6) || address_type(1) || ecdh_pubkey(32)

    The signature has 2 bits stored in byte 32 (the first byte of the scalar 's'
    in the Ed25519 signature format). We need to try sig_bits 0-3 to find which
    one produces a valid signature.

    Per the Flic 2 Protocol Specification:
    "If one combination passes, save signature[32] & 0x03 to the variable sigBits"

    Args:
        signature: 64-byte Ed25519 signature (with embedded sig_bits)
        address: 6-byte Bluetooth address
        address_type: 1-byte address type
        ecdh_pubkey: 32-byte X25519 public key

    Returns:
        sig_bits (0-3) that produced valid signature

    Raises:
        InvalidSignatureError: If no valid sig_bits found
    """
    from ..exceptions import InvalidSignatureError

    # Build message to verify
    message = address + bytes([address_type]) + ecdh_pubkey

    # Load Flic's public key
    public_key = Ed25519PublicKey.from_public_bytes(FLIC_PUBLIC_KEY)

    # Signature is 64 bytes: R (32 bytes) + s (32 bytes)
    # sig_bits is stored in the lowest 2 bits of byte 32 (first byte of scalar s)
    # We need to try all 4 possible values
    sig_array = bytearray(signature)

    for sig_bits in range(4):
        # Set bits 0-1 of byte 32 to current sig_bits value
        sig_array[32] = (signature[32] & 0xFC) | sig_bits

        try:
            public_key.verify(bytes(sig_array), message)
            return sig_bits
        except InvalidSignature:
            continue

    raise InvalidSignatureError("No valid sig_bits found for button identity signature")


def extract_sig_bits_from_signature(signature: bytes) -> int:
    """
    Extract sig_bits from a verified signature.

    Args:
        signature: 64-byte Ed25519 signature

    Returns:
        sig_bits value (0-3)
    """
    return signature[32] & 0x03
