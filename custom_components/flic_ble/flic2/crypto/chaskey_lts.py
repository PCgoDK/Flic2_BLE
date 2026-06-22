"""
Chaskey-LTS MAC implementation for Flic 2 protocol.

This is a MODIFIED Chaskey-LTS implementation that matches the Flic 2 protocol.
Key differences from standard Chaskey-LTS:

1. **Subkey generation**: Bits shift from MSB (v[3]) to LSB (v[0]), opposite of standard
2. **Permutation**: Uses different rotations than standard Chaskey-LTS
3. **Packet signatures**: Include direction (TX=1, RX=0) and counter in MAC

Reference implementation: flic2lib-c-module/flic2_crypto.c
Standard Chaskey: https://mouha.be/chaskey/
"""

import struct
from typing import List


def _rotl32(x: int, n: int) -> int:
    """32-bit left rotation."""
    x &= 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _rotr32(x: int, n: int) -> int:
    """32-bit right rotation."""
    x &= 0xFFFFFFFF
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF


def _times_two(key: List[int]) -> List[int]:
    """
    Multiply key by 2 in GF(2^128).

    The C code treats the 128-bit value with v[3] as the most significant word:
        c = (v[3] >> 31) * 0x87
        v[3] = (v[3] << 1) | (v[2] >> 31)
        v[2] = (v[2] << 1) | (v[1] >> 31)
        v[1] = (v[1] << 1) | (v[0] >> 31)
        v[0] = (v[0] << 1) ^ c
    """
    result = [0, 0, 0, 0]

    # Carry from MSB of v[3]
    c = (key[3] >> 31) * 0x87

    result[3] = ((key[3] << 1) | (key[2] >> 31)) & 0xFFFFFFFF
    result[2] = ((key[2] << 1) | (key[1] >> 31)) & 0xFFFFFFFF
    result[1] = ((key[1] << 1) | (key[0] >> 31)) & 0xFFFFFFFF
    result[0] = ((key[0] << 1) ^ c) & 0xFFFFFFFF

    return result


class ChaskeyLTS:
    """Chaskey-LTS 16-round MAC."""

    ROUNDS = 16

    def __init__(self, key: bytes):
        """
        Initialize Chaskey-LTS with a 16-byte key.

        Args:
            key: 16-byte key
        """
        if len(key) != 16:
            raise ValueError(f"Key must be 16 bytes, got {len(key)}")

        # Parse key as 4 little-endian 32-bit words
        self.k = list(struct.unpack("<4I", key))

        # Generate subkeys
        self.k1 = _times_two(self.k)
        self.k2 = _times_two(self.k1)

    def _permute(self, v: List[int]) -> List[int]:
        """
        Apply Flic's 16-round permutation.

        This is different from standard Chaskey-LTS. The Flic code uses:
            r6 = ROR32(r6, 16);  // pre-rotate
            for (16 rounds):
                r4 = r4 + r5;
                r5 = r4 ^ ROR32(r5, 27);
                r6 = r7 + ROR32(r6, 16);
                r7 = r6 ^ ROR32(r7, 24);
                r6 = r6 + r5;
                r4 = r7 + ROR32(r4, 16);
                r5 = r6 ^ ROR32(r5, 25);
                r7 = r4 ^ ROR32(r7, 19);
            r6 = ROR32(r6, 16);  // post-rotate
        """
        r4, r5, r6, r7 = v[0], v[1], v[2], v[3]

        # Pre-rotate r6
        r6 = _rotr32(r6, 16)

        for _ in range(self.ROUNDS):
            r4 = (r4 + r5) & 0xFFFFFFFF
            r5 = r4 ^ _rotr32(r5, 27)
            r6 = (r7 + _rotr32(r6, 16)) & 0xFFFFFFFF
            r7 = r6 ^ _rotr32(r7, 24)
            r6 = (r6 + r5) & 0xFFFFFFFF
            r4 = (r7 + _rotr32(r4, 16)) & 0xFFFFFFFF
            r5 = r6 ^ _rotr32(r5, 25)
            r7 = r4 ^ _rotr32(r7, 19)

        # Post-rotate r6
        r6 = _rotr32(r6, 16)

        return [r4, r5, r6, r7]

    def mac(self, message: bytes) -> bytes:
        """
        Compute Chaskey-LTS MAC.

        Args:
            message: Input message

        Returns:
            16-byte MAC (truncate to 5 bytes for Flic protocol)
        """
        # Initialize state with key
        v = self.k.copy()

        # Process full blocks
        block_size = 16
        i = 0
        while i + block_size <= len(message):
            block = struct.unpack("<4I", message[i:i + block_size])
            v[0] ^= block[0]
            v[1] ^= block[1]
            v[2] ^= block[2]
            v[3] ^= block[3]
            v = self._permute(v)
            i += block_size

        # Handle last block
        remaining = message[i:]
        last_block = bytearray(16)

        if len(remaining) < block_size:
            # Pad with 0x01 followed by zeros
            last_block[:len(remaining)] = remaining
            last_block[len(remaining)] = 0x01
            subkey = self.k2
        else:
            # Full last block
            last_block[:] = remaining
            subkey = self.k1

        # XOR with subkey and process
        block = list(struct.unpack("<4I", bytes(last_block)))
        v[0] ^= block[0] ^ subkey[0]
        v[1] ^= block[1] ^ subkey[1]
        v[2] ^= block[2] ^ subkey[2]
        v[3] ^= block[3] ^ subkey[3]
        v = self._permute(v)

        # XOR with key to produce tag
        v[0] ^= self.k[0]
        v[1] ^= self.k[1]
        v[2] ^= self.k[2]
        v[3] ^= self.k[3]

        return struct.pack("<4I", v[0], v[1], v[2], v[3])

    def mac5(self, message: bytes) -> bytes:
        """
        Compute 5-byte truncated MAC for Flic protocol.

        Args:
            message: Input message

        Returns:
            5-byte truncated MAC
        """
        return self.mac(message)[:5]

    def mac_with_dir_and_counter(self, message: bytes, direction: int, counter: int) -> bytes:
        """
        Compute 5-byte MAC with direction and packet counter.

        This is the Flic protocol's packet signature format:
        1. Initialize state from key
        2. XOR counter (64-bit) and direction into state
        3. Permute
        4. Process message blocks
        5. Output 5-byte MAC

        Args:
            message: Input message
            direction: 0 for RX, 1 for TX
            counter: 64-bit packet counter

        Returns:
            5-byte MAC
        """
        # Initialize state with key
        v = self.k.copy()

        # XOR counter (64-bit, little-endian) and direction
        v[0] ^= counter & 0xFFFFFFFF
        v[1] ^= (counter >> 32) & 0xFFFFFFFF
        v[2] ^= direction

        # Initial permutation
        v = self._permute(v)

        # Process full blocks
        block_size = 16
        i = 0
        while i + block_size < len(message):  # Note: strict < for this variant
            block = struct.unpack("<4I", message[i:i + block_size])
            v[0] ^= block[0]
            v[1] ^= block[1]
            v[2] ^= block[2]
            v[3] ^= block[3]
            v = self._permute(v)
            i += block_size

        # Handle last block
        remaining = message[i:]
        last_block = bytearray(16)

        if len(remaining) < block_size:
            # Pad with 0x01 followed by zeros
            last_block[:len(remaining)] = remaining
            last_block[len(remaining)] = 0x01
            subkey = self.k2
        else:
            # Full last block
            last_block[:] = remaining
            subkey = self.k1

        # XOR last block and subkey
        block = list(struct.unpack("<4I", bytes(last_block)))
        v[0] ^= block[0]
        v[1] ^= block[1]
        v[2] ^= block[2]
        v[3] ^= block[3]
        v[0] ^= subkey[0]
        v[1] ^= subkey[1]
        v[2] ^= subkey[2]
        v[3] ^= subkey[3]

        # Final permutation
        v = self._permute(v)

        # XOR with subkey again
        v[0] ^= subkey[0]
        v[1] ^= subkey[1]

        # Return first 5 bytes (v[0] as 4 bytes + low byte of v[1])
        return struct.pack("<IB", v[0], v[1] & 0xFF)

    def encrypt_block(self, plaintext: bytes) -> bytes:
        """
        Encrypt a single 16-byte block (used for quick verify key derivation).

        This implements chaskey_16_bytes from flic2lib-c:
        1. v = k ^ k1 ^ data
        2. permute(v)
        3. v ^= k1
        4. return v

        Args:
            plaintext: 16-byte plaintext

        Returns:
            16-byte ciphertext
        """
        if len(plaintext) != 16:
            raise ValueError(f"Block must be 16 bytes, got {len(plaintext)}")

        # XOR with key and k1
        block = list(struct.unpack("<4I", plaintext))
        v = [
            block[0] ^ self.k[0] ^ self.k1[0],
            block[1] ^ self.k[1] ^ self.k1[1],
            block[2] ^ self.k[2] ^ self.k1[2],
            block[3] ^ self.k[3] ^ self.k1[3],
        ]

        # Permute
        v = self._permute(v)

        # XOR with k1 again
        v[0] ^= self.k1[0]
        v[1] ^= self.k1[1]
        v[2] ^= self.k1[2]
        v[3] ^= self.k1[3]

        return struct.pack("<4I", v[0], v[1], v[2], v[3])
