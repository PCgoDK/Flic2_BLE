"""Key generation and derivation for Flic 2 protocol."""

import hashlib
import hmac
import os
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from .chaskey_lts import ChaskeyLTS


def generate_keypair() -> Tuple[bytes, bytes]:
    """
    Generate X25519 keypair for ECDH.

    Returns:
        Tuple of (private_key_bytes, public_key_bytes)
    """
    private_key = X25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes_raw()
    public_bytes = public_key.public_bytes_raw()

    return private_bytes, public_bytes


def compute_shared_secret(
    our_private_key: bytes,
    their_public_key: bytes,
) -> bytes:
    """
    Compute X25519 shared secret.

    Args:
        our_private_key: Our 32-byte X25519 private key
        their_public_key: Their 32-byte X25519 public key

    Returns:
        32-byte shared secret
    """
    private_key = X25519PrivateKey.from_private_bytes(our_private_key)
    public_key = X25519PublicKey.from_public_bytes(their_public_key)

    return private_key.exchange(public_key)


def derive_full_verify_secret(
    shared_secret: bytes,
    sig_bits: int,
    button_random: bytes,
    client_random: bytes,
) -> bytes:
    """
    Derive the full verify secret.

    fullVerifySecret = SHA256(shared_secret || sig_bits || button_random || client_random || 0x00)

    Args:
        shared_secret: 32-byte X25519 shared secret
        sig_bits: 1 byte, the sig_bits from Ed25519 verification
        button_random: 8-byte random from button
        client_random: 8-byte random we generated

    Returns:
        32-byte full verify secret
    """
    data = (
        shared_secret +
        bytes([sig_bits]) +
        button_random +
        client_random +
        bytes([0x00])
    )
    return hashlib.sha256(data).digest()


def derive_verifier(full_verify_secret: bytes) -> bytes:
    """
    Derive verifier for full verify request 2.

    verifier = HMAC-SHA256(fullVerifySecret, "AT")[:16]

    Args:
        full_verify_secret: 32-byte full verify secret

    Returns:
        16-byte verifier
    """
    return hmac.new(full_verify_secret, b"AT", hashlib.sha256).digest()[:16]


def derive_session_key(full_verify_secret: bytes) -> bytes:
    """
    Derive session key for MAC after pairing.

    session_key = HMAC-SHA256(fullVerifySecret, "SK")[:16]

    Args:
        full_verify_secret: 32-byte full verify secret

    Returns:
        16-byte session key
    """
    return hmac.new(full_verify_secret, b"SK", hashlib.sha256).digest()[:16]


def derive_pairing_data(full_verify_secret: bytes) -> Tuple[bytes, bytes]:
    """
    Derive pairing ID and key for storage.

    pairing_data = HMAC-SHA256(fullVerifySecret, "PK")[:20]
    pairing_id = pairing_data[:4]
    pairing_key = pairing_data[4:20]

    Args:
        full_verify_secret: 32-byte full verify secret

    Returns:
        Tuple of (pairing_id[4], pairing_key[16])
    """
    pairing_data = hmac.new(full_verify_secret, b"PK", hashlib.sha256).digest()[:20]
    pairing_id = pairing_data[:4]
    pairing_key = pairing_data[4:20]
    return pairing_id, pairing_key


def derive_quick_verify_session_key(
    pairing_key: bytes,
    client_random: bytes,
    button_random: bytes,
) -> bytes:
    """
    Derive session key for quick verify.

    The session key is derived using Chaskey-LTS encryption:
    session_key = Chaskey-LTS-Encrypt(pairing_key, client_random(7) || 0x00 || button_random(8))

    Args:
        pairing_key: 16-byte stored pairing key
        client_random: 7-byte random we sent (first 7 bytes)
        button_random: 8-byte random from button

    Returns:
        16-byte session key
    """
    # Build the plaintext block
    # client_random should be 7 bytes, button_random should be 8 bytes
    # Total: 7 + 1 (padding) + 8 = 16 bytes
    if len(client_random) < 7:
        raise ValueError(f"client_random must be at least 7 bytes, got {len(client_random)}")
    if len(button_random) < 8:
        raise ValueError(f"button_random must be at least 8 bytes, got {len(button_random)}")

    plaintext = client_random[:7] + bytes([0x00]) + button_random[:8]

    # Encrypt using Chaskey-LTS
    chaskey = ChaskeyLTS(pairing_key)
    return chaskey.encrypt_block(plaintext)


def generate_random(length: int) -> bytes:
    """
    Generate cryptographically secure random bytes.

    Args:
        length: Number of bytes to generate

    Returns:
        Random bytes
    """
    return os.urandom(length)
