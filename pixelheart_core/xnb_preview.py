"""Bounded, read-only XNB texture and tBIN payload decoding for local previews.

This narrow decoder implements XNA's public container/texture formats and LZX
framing. It never instantiates content readers or executes asset data. Reference
formats: MonoGame ContentManager/Texture2DReader and MS-PATCH LZX specification.
Game content is always supplied locally, never bundled with this module.
"""
from __future__ import annotations

import io
import struct
from PIL import Image

MAX_PACKED = 16 * 1024 * 1024
MAX_DECODED = 64 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024


class XnbError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data = memoryview(data)
        self.pos = 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise XnbError("The game asset is truncated.")
        out = self.data[self.pos:self.pos + size].tobytes()
        self.pos += size
        return out

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def byte(self):
        return self.number('B')

    def count(self, maximum):
        n = self.number('i')
        if not 0 <= n <= maximum:
            raise XnbError("The game asset exceeds the supported item limit.")
        return n

    def seven(self):
        value = 0
        for shift in range(0, 35, 7):
            b = self.byte()
            value |= (b & 127) << shift
            if not b & 128:
                return value
        raise XnbError("The game asset has an invalid integer.")

    def string(self, *, tbin=False):
        size = self.count(65536) if tbin else self.seven()
        if size > 65536:
            raise XnbError("The game asset has an oversized string.")
        try:
            return self.take(size).decode('utf-8')
        except UnicodeError as exc:
            raise XnbError("The game asset has an invalid string.") from exc


class _Bits:
    def __init__(self, payload):
        self.reader = Reader(payload)
        self.value = 0
        self.remaining = 0

    def read(self, count):
        while self.remaining < count:
            self.value = (self.value << 16) | self.reader.number('H')
            self.remaining += 16
        self.remaining -= count
        result = self.value >> self.remaining
        self.value &= (1 << self.remaining) - 1
        return result

    def align_raw(self):
        # An uncompressed LZX block consumes the next 1..16 padding bits.
        if self.remaining == 0:
            self.reader.take(2)
        self.remaining = self.value = 0


class _Huffman:
    def __init__(self, lengths):
        self.codes = {}
        self.maximum = max(lengths, default=0)
        code = 0
        for width in range(1, self.maximum + 1):
            for symbol, length in enumerate(lengths):
                if length == width:
                    if code >= 1 << width:
                        raise XnbError("The game asset has an invalid Huffman tree.")
                    self.codes[(width, code)] = symbol
                    code += 1
            code <<= 1

    def read(self, bits):
        code = 0
        for width in range(1, self.maximum + 1):
            code = (code << 1) | bits.read(1)
            symbol = self.codes.get((width, code))
            if symbol is not None:
                return symbol
        raise XnbError("The game asset has an invalid compressed symbol.")


def _lengths(bits, values, start, end):
    tree = _Huffman([bits.read(4) for _ in range(20)])
    while start < end:
        code = tree.read(bits)
        count = 1
        if code == 17:
            count, value = bits.read(4) + 4, 0
        elif code == 18:
            count, value = bits.read(5) + 20, 0
        elif code == 19:
            count = bits.read(1) + 4
            value = (values[start] - tree.read(bits)) % 17
        else:
            value = (values[start] - code) % 17
        if start + count > end:
            raise XnbError("The game asset has invalid compressed code lengths.")
        values[start:start + count] = [value] * count
        start += count


def _lzx(payload, expected):
    frames = Reader(payload)
    history = bytearray()
    main_lengths, length_lengths = [0] * 512, [0] * 249
    offsets = [1, 1, 1]
    extras = [max(0, (slot // 2) - 1) for slot in range(32)]
    bases, position = [], 0
    for count in extras:
        bases.append(position)
        position += 1 << count
    remaining = block_length = kind = 0
    first = True
    while len(history) < expected:
        marker = frames.byte()
        if marker == 255:
            output_size = (frames.byte() << 8) | frames.byte()
            input_size = (frames.byte() << 8) | frames.byte()
        else:
            input_size = (marker << 8) | frames.byte()
            output_size = 32768
        if not 0 < output_size <= 32768 or not 0 < input_size <= 65536 or len(history) + output_size > expected:
            raise XnbError("The game asset has an invalid LZX frame.")
        bits = _Bits(frames.take(input_size))
        if first:
            first = False
            if bits.read(1) and ((bits.read(16) << 16) | bits.read(16)):
                raise XnbError("Executable LZX transforms are not supported for scene assets.")
        finish = len(history) + output_size
        while len(history) < finish:
            if remaining == 0:
                if kind == 3 and block_length % 2:
                    bits.reader.take(1)
                kind = bits.read(3)
                block_length = remaining = (bits.read(16) << 8) | bits.read(8)
                if remaining <= 0:
                    raise XnbError("The game asset has an empty LZX block.")
                if kind == 2:
                    aligned = _Huffman([bits.read(3) for _ in range(8)])
                if kind in (1, 2):
                    _lengths(bits, main_lengths, 0, 256)
                    _lengths(bits, main_lengths, 256, 512)
                    main = _Huffman(main_lengths)
                    _lengths(bits, length_lengths, 0, 249)
                    length = _Huffman(length_lengths)
                elif kind == 3:
                    bits.align_raw()
                    offsets = [bits.reader.number('I') for _ in range(3)]
                else:
                    raise XnbError("The game asset has an unsupported LZX block.")
            run = min(remaining, finish - len(history))
            if kind == 3:
                history.extend(bits.reader.take(run))
                remaining -= run
                continue
            stop = len(history) + run
            while len(history) < stop:
                symbol = main.read(bits)
                if symbol < 256:
                    history.append(symbol)
                    remaining -= 1
                    continue
                symbol -= 256
                count, slot = symbol & 7, symbol >> 3
                count += (length.read(bits) if count == 7 else 0) + 2
                if slot < 3:
                    distance = offsets[slot]
                    if slot:
                        offsets[slot] = offsets[0]
                        offsets[0] = distance
                else:
                    extra = extras[slot]
                    distance = bases[slot] - 2
                    if kind == 2 and extra >= 3:
                        distance += (bits.read(extra - 3) << 3) + aligned.read(bits)
                    else:
                        distance += bits.read(extra) if extra else 1 - distance
                    offsets = [distance, offsets[0], offsets[1]]
                if not 1 <= distance <= min(65536, len(history)) or len(history) + count > stop:
                    raise XnbError("The game asset has an invalid LZX match.")
                # Repeating source slices handle overlapping matches without
                # per-pixel work, and remain bounded by the declared output.
                pattern = bytes(history[-distance:])
                history.extend((pattern * ((count + distance - 1) // distance))[:count])
                remaining -= count
    if frames.pos < len(frames.data) and any(frames.take(len(frames.data) - frames.pos)):
        raise XnbError("Unexpected data follows the compressed game asset.")
    return bytes(history)


def _lz4(payload, expected):
    reader, result = Reader(payload), bytearray()
    def amount(base):
        value = base
        if base == 15:
            while True:
                b = reader.byte()
                value += b
                if value > expected:
                    raise XnbError("The game asset exceeds its decoded size.")
                if b != 255:
                    break
        return value
    while reader.pos < len(reader.data):
        token = reader.byte()
        size = amount(token >> 4)
        if len(result) + size > expected:
            raise XnbError("The game asset exceeds its decoded size.")
        result.extend(reader.take(size))
        if reader.pos == len(reader.data):
            break
        distance = reader.number('H')
        size = amount(token & 15) + 4
        if not 0 < distance <= len(result) or len(result) + size > expected:
            raise XnbError("The game asset has an invalid LZ4 match.")
        source = bytes(result[-distance:])
        result.extend((source * ((size + distance - 1) // distance))[:size])
    if len(result) != expected:
        raise XnbError("The game asset has an incomplete decoded payload.")
    return bytes(result)


def _xnb_object(payload):
    """Return (reader type name, bounded primary object payload)."""
    if len(payload) > MAX_PACKED:
        raise XnbError("The game asset exceeds the compressed size limit.")
    reader = Reader(payload)
    if reader.take(3) != b'XNB' or reader.byte() not in b'wmxadi':
        raise XnbError("This is not a supported XNB game asset.")
    if reader.byte() not in (4, 5):
        raise XnbError("This XNB version is not supported.")
    flags = reader.byte()
    if reader.number('I') != len(payload) or flags & 0x3E or flags & 0xC0 == 0xC0:
        raise XnbError("The XNB header is invalid.")
    if flags & 0xC0:
        expected = reader.number('I')
        if not 0 < expected <= MAX_DECODED:
            raise XnbError("The game asset exceeds the decoded size limit.")
        packed = reader.take(len(payload) - reader.pos)
        reader = Reader(_lzx(packed, expected) if flags & 0x80 else _lz4(packed, expected))
    count = reader.seven()
    if not 1 <= count <= 32:
        raise XnbError("The XNB reader table is invalid.")
    types = []
    for _ in range(count):
        types.append(reader.string())
        reader.number('i')
    if reader.seven() != 0:
        raise XnbError("Shared XNB resources are unsupported for scene previews.")
    primary = reader.seven()
    if not 1 <= primary <= len(types):
        raise XnbError("The XNB primary reader is invalid.")
    return types, primary, reader.take(len(reader.data) - reader.pos)


def decode_xnb(payload):
    """Return the primary reader name and its bounded, decompressed payload."""
    types, primary, data = _xnb_object(payload)
    return types[primary - 1].split(',')[0], data


def string_dictionary(payload, *, maximum=10000):
    """Read a String→String XNA dictionary without instantiating content readers.

    Nested object tags must point to an actual StringReader, not just the reader
    index used by one game build. No other generic types or shared resources are
    accepted. The container and individual strings retain the preview limits.
    """
    if type(maximum) is not int or not 0 <= maximum <= 10000:
        raise XnbError("Choose a dictionary limit from 0 to 10000.")
    types, primary, data = _xnb_object(payload)
    kind = types[primary - 1]
    prefix = 'Microsoft.Xna.Framework.Content.DictionaryReader`2[['
    if not kind.startswith(prefix):
        raise XnbError("The game asset is not a string dictionary.")
    # Assembly-qualified generic arguments contain commas; splitting the type
    # name at its first comma would lose the second argument's identity.
    if ']]' not in kind[len(prefix):]:
        raise XnbError("The game dictionary reader type is malformed.")
    arguments = kind[len(prefix):].split(']]', 1)[0].split('],[')
    if len(arguments) != 2 or any(arg.split(',')[0].strip() != 'System.String' for arg in arguments):
        raise XnbError("The game asset is not a String→String dictionary.")
    reader, result = Reader(data), {}
    for _ in range(reader.count(maximum)):
        pair = []
        for _ in range(2):
            index = reader.seven()
            if not 1 <= index <= len(types) or types[index - 1].split(',')[0] != 'Microsoft.Xna.Framework.Content.StringReader':
                raise XnbError("The game dictionary contains an unsupported object.")
            pair.append(reader.string())
        key, value = pair
        if key in result:
            raise XnbError("The game dictionary contains duplicate keys.")
        result[key] = value
    if reader.pos != len(reader.data):
        raise XnbError("Unexpected data follows the game dictionary.")
    return result


def texture_image(payload):
    """Decode a local XNB Texture2D into an independent straight-alpha RGBA image."""
    kind, data = decode_xnb(payload)
    if kind != 'Microsoft.Xna.Framework.Content.Texture2DReader':
        raise XnbError("The game asset is not a Texture2D image.")
    reader = Reader(data)
    fmt, width, height, levels = [reader.number('i') for _ in range(4)]
    if not 0 < width <= 8192 or not 0 < height <= 8192 or width * height > MAX_PIXELS or not 1 <= levels <= 16:
        raise XnbError("The game texture has unsupported dimensions.")
    pixels = reader.take(reader.count(MAX_DECODED))
    if fmt == 0:
        if len(pixels) != width * height * 4:
            raise XnbError("The game texture has invalid pixel data.")
        image = Image.frombytes('RGBA', (width, height), pixels)
    elif fmt in (4, 5, 6):
        if len(pixels) != ((width + 3) // 4) * ((height + 3) // 4) * (8 if fmt == 4 else 16):
            raise XnbError("The game texture has invalid compressed pixel data.")
        # Pillow's maintained DDS reader handles DXT1/3/5 blocks.
        fourcc = {4: b'DXT1', 5: b'DXT3', 6: b'DXT5'}[fmt]
        header = struct.pack('<7I', 124, 0x81007, height, width, len(pixels), 0, 1) + bytes(44)
        header += struct.pack('<2I4s5I', 32, 4, fourcc, 0, 0, 0, 0, 0)
        header += struct.pack('<5I', 0x1000, 0, 0, 0, 0)
        with Image.open(io.BytesIO(b'DDS ' + header + pixels)) as dds:
            image = dds.convert('RGBA')
    else:
        raise XnbError("This game texture format is not supported.")
    for _ in range(1, levels):
        reader.take(reader.count(MAX_DECODED))
    if reader.pos != len(reader.data):
        raise XnbError("Unexpected data follows the game texture.")
    # XNA Content Pipeline stores premultiplied RGBA. Restore straight alpha for
    # Pillow compositing; solid pixel art takes the no-op fast path.
    alpha = image.getchannel('A')
    if sum(alpha.histogram()[1:255]):
        rgba = bytearray(image.tobytes())
        for i in range(0, len(rgba), 4):
            a = rgba[i + 3]
            if 0 < a < 255:
                for c in range(3):
                    rgba[i + c] = min(255, (rgba[i + c] * 255 + a // 2) // a)
        image = Image.frombytes('RGBA', (width, height), bytes(rgba))
    return image


def map_payload(payload):
    kind, data = decode_xnb(payload)
    if kind not in ('xTile.Pipeline.TideReader', 'xTile.Pipeline.TbinReader'):
        raise XnbError("The game asset is not a supported tile map.")
    reader = Reader(data)
    result = reader.take(reader.count(MAX_DECODED))
    if reader.pos != len(reader.data):
        raise XnbError("Unexpected data follows the game map.")
    return result
