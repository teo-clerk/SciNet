"""The BZZ decompressor, so DjVu text layers can be read without a C library.

DjVu stores its OCR text in a ``TXTz`` chunk compressed with BZZ — DjVu's
general-purpose codec, a Burrows-Wheeler transform coded with the ZP adaptive
binary arithmetic coder. Nothing on PyPI decodes it in pure Python:
``python-djvulibre`` ships source only and needs the DjVuLibre C library and a
compiler, which is exactly the dependency a project that has to zip up and run
on someone else's Windows machine cannot take.

So it is implemented here. It is about two hundred lines and it has no
dependencies at all, which is a good trade for removing a build toolchain from
the install path.

The state table below is a constant of the DjVu bitstream format rather than a
tuning choice: every conforming ZP decoder holds these exact 251 entries, the
way every CRC32 implementation holds the same polynomial. Decoding is verified
byte-for-byte against DjVuLibre's own ``bzz`` encoder in
``tests/test_djvu.py``, including on incompressible input, which is the case
that exercises the least-probable-symbol path the compressible cases never
reach.
"""

from __future__ import annotations

# fmt: off
#: Probability increment per state.
P = (
    0x8000, 0x8000, 0x8000, 0x6BBD, 0x6BBD, 0x5D45, 0x5D45, 0x51B9, 0x51B9,
    0x4813, 0x4813, 0x3FD5, 0x3FD5, 0x38B1, 0x38B1, 0x3275, 0x3275, 0x2CFD,
    0x2CFD, 0x2825, 0x2825, 0x23AB, 0x23AB, 0x1F87, 0x1F87, 0x1BBB, 0x1BBB,
    0x1845, 0x1845, 0x1523, 0x1523, 0x1253, 0x1253, 0x0FCF, 0x0FCF, 0x0D95,
    0x0D95, 0x0B9D, 0x0B9D, 0x09E3, 0x09E3, 0x0861, 0x0861, 0x0711, 0x0711,
    0x05F1, 0x05F1, 0x04F9, 0x04F9, 0x0425, 0x0425, 0x0371, 0x0371, 0x02D9,
    0x02D9, 0x0259, 0x0259, 0x01ED, 0x01ED, 0x0193, 0x0193, 0x0149, 0x0149,
    0x010B, 0x010B, 0x00D5, 0x00D5, 0x00A5, 0x00A5, 0x007B, 0x007B, 0x0057,
    0x0057, 0x003B, 0x003B, 0x0023, 0x0023, 0x0013, 0x0013, 0x0007, 0x0007,
    0x0001, 0x0001, 0x5695, 0x24EE, 0x8000, 0x0D30, 0x481A, 0x0481, 0x3579,
    0x017A, 0x24EF, 0x007B, 0x1978, 0x0028, 0x10CA, 0x000D, 0x0B5D, 0x0034,
    0x078A, 0x00A0, 0x050F, 0x0117, 0x0358, 0x01EA, 0x0234, 0x0144, 0x0173,
    0x0234, 0x00F5, 0x0353, 0x00A1, 0x05C5, 0x011A, 0x03CF, 0x01AA, 0x0285,
    0x0286, 0x01AB, 0x03D3, 0x011A, 0x05C5, 0x00BA, 0x08AD, 0x007A, 0x0CCC,
    0x01EB, 0x1302, 0x02E6, 0x1B81, 0x045E, 0x24EF, 0x0690, 0x2865, 0x09DE,
    0x3987, 0x0DC8, 0x2C99, 0x10CA, 0x3B5F, 0x0B5D, 0x5695, 0x078A, 0x8000,
    0x050F, 0x24EE, 0x0358, 0x0D30, 0x0234, 0x0481, 0x0173, 0x017A, 0x00F5,
    0x007B, 0x00A1, 0x0028, 0x011A, 0x000D, 0x01AA, 0x0034, 0x0286, 0x00A0,
    0x03D3, 0x0117, 0x05C5, 0x01EA, 0x08AD, 0x0144, 0x0CCC, 0x0234, 0x1302,
    0x0353, 0x1B81, 0x05C5, 0x24EF, 0x03CF, 0x2B74, 0x0285, 0x201D, 0x01AB,
    0x1715, 0x011A, 0x0FB7, 0x00BA, 0x0A67, 0x01EB, 0x06E7, 0x02E6, 0x0496,
    0x045E, 0x030D, 0x0690, 0x0206, 0x09DE, 0x0155, 0x0DC8, 0x00E1, 0x2B74,
    0x0094, 0x201D, 0x0188, 0x1715, 0x0252, 0x0FB7, 0x0383, 0x0A67, 0x0547,
    0x06E7, 0x07E2, 0x0496, 0x0BC0, 0x030D, 0x1178, 0x0206, 0x19DA, 0x0155,
    0x24EF, 0x00E1, 0x320E, 0x0094, 0x432A, 0x0188, 0x447D, 0x0252, 0x5ECE,
    0x0383, 0x8000, 0x0547, 0x481A, 0x07E2, 0x3579, 0x0BC0, 0x24EF, 0x1178,
    0x1978, 0x19DA, 0x2865, 0x24EF, 0x3987, 0x320E, 0x2C99, 0x432A, 0x3B5F,
    0x447D, 0x5695, 0x5ECE, 0x8000, 0x8000, 0x5695, 0x481A, 0x481A
)

#: Adaptation threshold: the most-probable symbol only advances above this.
M = (
    0x0000, 0x0000, 0x0000, 0x10A5, 0x10A5, 0x1F28, 0x1F28, 0x2BD3, 0x2BD3,
    0x36E3, 0x36E3, 0x408C, 0x408C, 0x48FD, 0x48FD, 0x505D, 0x505D, 0x56D0,
    0x56D0, 0x5C71, 0x5C71, 0x615B, 0x615B, 0x65A5, 0x65A5, 0x6962, 0x6962,
    0x6CA2, 0x6CA2, 0x6F74, 0x6F74, 0x71E6, 0x71E6, 0x7404, 0x7404, 0x75D6,
    0x75D6, 0x7768, 0x7768, 0x78C2, 0x78C2, 0x79EA, 0x79EA, 0x7AE7, 0x7AE7,
    0x7BBE, 0x7BBE, 0x7C75, 0x7C75, 0x7D0F, 0x7D0F, 0x7D91, 0x7D91, 0x7DFE,
    0x7DFE, 0x7E5A, 0x7E5A, 0x7EA6, 0x7EA6, 0x7EE6, 0x7EE6, 0x7F1A, 0x7F1A,
    0x7F45, 0x7F45, 0x7F6B, 0x7F6B, 0x7F8D, 0x7F8D, 0x7FAA, 0x7FAA, 0x7FC3,
    0x7FC3, 0x7FD7, 0x7FD7, 0x7FE7, 0x7FE7, 0x7FF2, 0x7FF2, 0x7FFA, 0x7FFA,
    0x7FFF, 0x7FFF, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000,
    0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000, 0x0000
)

#: Next state after a most-probable / least-probable symbol.
UP = (
    84, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22,
    23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41,
    42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60,
    61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79,
    80, 81, 82, 81, 82, 9, 86, 5, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 82,
    99, 76, 101, 70, 103, 66, 105, 106, 107, 66, 109, 60, 111, 56, 69, 114, 65,
    116, 61, 118, 57, 120, 53, 122, 49, 124, 43, 72, 39, 60, 33, 56, 29, 52, 23,
    48, 23, 42, 137, 38, 21, 140, 15, 142, 9, 144, 141, 146, 147, 148, 149, 150,
    151, 152, 153, 154, 155, 70, 157, 66, 81, 62, 75, 58, 69, 54, 65, 50, 167,
    44, 65, 40, 59, 34, 55, 30, 175, 24, 177, 178, 179, 180, 181, 182, 183, 184,
    69, 186, 59, 188, 55, 190, 51, 192, 47, 194, 41, 196, 37, 198, 199, 72, 201,
    62, 203, 58, 205, 54, 207, 50, 209, 46, 211, 40, 213, 36, 215, 30, 217, 26,
    219, 20, 71, 14, 61, 14, 57, 8, 53, 228, 49, 230, 45, 232, 39, 234, 35, 138,
    29, 24, 25, 240, 19, 22, 13, 16, 13, 10, 7, 244, 249, 10, 89, 230
)

DN = (
    145, 4, 3, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
    19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37,
    38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56,
    57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75,
    76, 77, 78, 79, 80, 85, 226, 6, 176, 143, 138, 141, 112, 135, 104, 133, 100,
    129, 98, 127, 72, 125, 102, 123, 60, 121, 110, 119, 108, 117, 54, 115, 48,
    113, 134, 59, 132, 55, 130, 51, 128, 47, 126, 41, 62, 37, 66, 31, 54, 25,
    50, 131, 46, 17, 40, 15, 136, 7, 32, 139, 172, 9, 170, 85, 168, 248, 166,
    247, 164, 197, 162, 95, 160, 173, 158, 165, 156, 161, 60, 159, 56, 71, 52,
    163, 48, 59, 42, 171, 38, 169, 32, 53, 26, 47, 174, 193, 18, 191, 222, 189,
    218, 187, 216, 185, 214, 61, 212, 53, 210, 49, 208, 45, 206, 39, 204, 195,
    202, 31, 200, 243, 64, 239, 56, 237, 52, 235, 48, 233, 44, 231, 38, 229, 34,
    227, 28, 225, 22, 223, 16, 221, 220, 63, 8, 55, 224, 51, 2, 47, 87, 43, 246,
    37, 244, 33, 238, 27, 236, 21, 16, 15, 8, 241, 242, 7, 10, 245, 2, 1, 83,
    250, 2, 143, 246
)
# fmt: on

#: Leading one-bits in a byte — the renormalisation shift after a
#: least-probable symbol widens the interval.
_LEADING_ONES = tuple(
    next(n for n in range(9) if n == 8 or not (i << n) & 0x80) for i in range(256)
)

_HALF = 0x8000
_MASK16 = 0xFFFF


class CorruptStream(ValueError):
    """The byte stream is not valid BZZ."""


class ZPDecoder:
    """DjVu's ZP adaptive binary arithmetic decoder.

    Each context is one byte of state: the low bit is the symbol currently
    considered most probable, and the whole byte indexes the tables above. That
    is why ``UP``/``DN`` give a state directly — the symbol flip is folded into
    the transition, so adaptation never needs a separate branch.
    """

    __slots__ = (
        "_data",
        "_ptr",
        "_a",
        "_code",
        "_fence",
        "_scount",
        "_buffer",
        "_delay",
    )

    #: Bytes of 0xFF fed past the end before the stream is declared truncated.
    #: The encoder relies on the decoder reading slightly beyond what it wrote.
    OVERRUN_BYTES = 25

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._ptr = 0
        self._a = 0
        self._scount = 0
        self._buffer = 0
        self._delay = self.OVERRUN_BYTES
        self._preload()
        self._code = (self._buffer >> (self._scount - 16)) & _MASK16
        self._scount -= 16
        self._set_fence()

    def _set_fence(self) -> None:
        self._fence = 0x7FFF if self._code >= _HALF else self._code

    def _preload(self) -> None:
        while self._scount <= 24:
            if self._ptr >= len(self._data):
                self._delay -= 1
                if self._delay < 1:
                    raise CorruptStream("BZZ stream ended mid-symbol")
                self._buffer = ((self._buffer << 8) | 0xFF) & 0xFFFFFFFFFF
            else:
                self._buffer = (
                    (self._buffer << 8) | self._data[self._ptr]
                ) & 0xFFFFFFFFFF
                self._ptr += 1
            self._scount += 8

    def decode(self, contexts: list[int], index: int) -> int:
        """One bit under an adaptive context, which this call updates."""
        state = contexts[index]
        z = self._a + P[state]
        if z <= self._fence:
            # Fast path: the interval did not need renormalising, so no state
            # changes and no bits are consumed.
            self._a = z
            return state & 1
        return self._decode_slow(contexts, index, z)

    def decode_flat(self) -> int:
        """One bit with no context — both outcomes equally likely."""
        return self._decode_slow(None, 0, _HALF + (self._a >> 1))

    def _decode_slow(self, contexts: list[int] | None, index: int, z: int) -> int:
        state = contexts[index] if contexts is not None else 0
        bit = state & 1
        ceiling = 0x6000 + ((z + self._a) >> 2)
        z = min(z, ceiling)

        if z > self._code:
            bit ^= 1
            z = 0x10000 - z
            self._a = (self._a + z) & _MASK16
            self._code = (self._code + z) & _MASK16
            if contexts is not None:
                contexts[index] = DN[state]
            shift = (
                _LEADING_ONES[self._a & 0xFF] + 8
                if self._a >= 0xFF00
                else _LEADING_ONES[(self._a >> 8) & 0xFF]
            )
            self._scount -= shift
            self._a = (self._a << shift) & _MASK16
            self._code = ((self._code << shift) & _MASK16) | (
                (self._buffer >> self._scount) & ((1 << shift) - 1)
            )
        else:
            if contexts is not None and self._a >= M[state]:
                contexts[index] = UP[state]
            self._scount -= 1
            self._a = (z << 1) & _MASK16
            self._code = ((self._code << 1) & _MASK16) | (
                (self._buffer >> self._scount) & 1
            )

        self._preload()
        self._set_fence()
        return bit


#: How many symbols keep an explicit frequency estimate. Beyond this the
#: move-to-front list is rotated positionally rather than by frequency.
_FREQ_TRACKED = 4
#: Contexts reserved for "is this the most recent symbol" at three recency
#: levels. Recency is the whole point of a move-to-front code: the coder is far
#: better calibrated when it knows whether the last symbol repeated.
_RECENCY_CONTEXTS = 3
_MAX_BLOCK_BYTES = 4096 * 1024
_ESCAPE = 256


def _decode_flat_int(zp: ZPDecoder, bits: int) -> int:
    value, limit = 1, 1 << bits
    while value < limit:
        value = (value << 1) | zp.decode_flat()
    return value - limit


def _decode_int(zp: ZPDecoder, contexts: list[int], base: int, bits: int) -> int:
    """An integer coded as a binary tree, one adaptive context per tree node."""
    value, limit = 1, 1 << bits
    while value < limit:
        value = (value << 1) | zp.decode(contexts, base + value - 1)
    return value - limit


def decompress(payload: bytes) -> bytes:
    """Decode a complete BZZ stream — a sequence of blocks ending in a zero."""
    zp = ZPDecoder(payload)
    contexts = [0] * 300
    out = bytearray()
    while True:
        size = _decode_flat_int(zp, 24)
        if size == 0:
            return bytes(out)
        if size > _MAX_BLOCK_BYTES:
            raise CorruptStream(f"BZZ block of {size} bytes is implausible")
        out += _decode_block(zp, contexts, size)


def _decode_block(zp: ZPDecoder, contexts: list[int], size: int) -> bytes:
    # How fast the frequency estimates decay, chosen by the encoder.
    speed = 0
    if zp.decode_flat():
        speed += 1
        if zp.decode_flat():
            speed += 1

    recent = list(range(256))
    freq = [0] * _FREQ_TRACKED
    increment = 4
    rank = 3
    marker = -1
    data = bytearray(size)

    for i in range(size):
        rank = _decode_rank(zp, contexts, rank)
        if rank == _ESCAPE:
            # The BWT's rotation marker: where the original string began.
            marker = i
            continue
        data[i] = recent[rank]

        increment += increment >> speed
        if increment > 0x10000000:
            increment >>= 24
            freq = [f >> 24 for f in freq]
        weight = increment + (freq[rank] if rank < _FREQ_TRACKED else 0)

        # Move the symbol forward: positionally while it is outside the tracked
        # window, then by frequency once inside it.
        k = rank
        while k >= _FREQ_TRACKED:
            recent[k] = recent[k - 1]
            k -= 1
        while k > 0 and weight >= freq[k - 1]:
            recent[k] = recent[k - 1]
            freq[k] = freq[k - 1]
            k -= 1
        recent[k] = data[i]
        freq[k] = weight

    if not 0 < marker < size:
        raise CorruptStream(f"BZZ block has no usable marker (got {marker})")
    return _inverse_burrows_wheeler(data, size, marker)


def _decode_rank(zp: ZPDecoder, contexts: list[int], previous: int) -> int:
    """The move-to-front rank of the next symbol, or 256 for the marker.

    Coded as a unary escape over widening buckets: rank 0 and rank 1 get a bit
    each — together they are most of a Burrows-Wheeler stream — and everything
    above is a bucket index followed by an offset within it.
    """
    recency = min(_RECENCY_CONTEXTS - 1, previous)
    base = 0
    if zp.decode(contexts, base + recency):
        return 0
    base += _RECENCY_CONTEXTS
    if zp.decode(contexts, base + recency):
        return 1
    base += _RECENCY_CONTEXTS

    for bits in range(1, 8):
        if zp.decode(contexts, base):
            return (1 << bits) + _decode_int(zp, contexts, base + 1, bits)
        base += 1 << bits
    return _ESCAPE


def _inverse_burrows_wheeler(data: bytearray, size: int, marker: int) -> bytes:
    """Undo the sort transform, walking the permutation back from the end."""
    position = [0] * size
    counts = [0] * 256
    for i in range(size):
        if i == marker:
            continue
        symbol = data[i]
        position[i] = (symbol << 24) | (counts[symbol] & 0xFFFFFF)
        counts[symbol] += 1

    running = 1
    for symbol in range(256):
        counts[symbol], running = running, running + counts[symbol]

    out = bytearray(size - 1)
    cursor = 0
    for index in range(size - 2, -1, -1):
        entry = position[cursor]
        symbol = entry >> 24
        out[index] = symbol
        cursor = counts[symbol] + (entry & 0xFFFFFF)
    if cursor != marker:
        raise CorruptStream("BZZ inverse transform did not close the cycle")
    return bytes(out)
