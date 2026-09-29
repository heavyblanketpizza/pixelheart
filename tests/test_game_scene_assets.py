"""Synthetic game-format fixtures: no copied Stardew Valley assets required."""
from copy import deepcopy
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from pixelheart_core.game_scene_assets import (
    asset_path, clear_game_cache, compose_farmer, content_root, discover_game_root,
    load_farmer, load_game_map, load_texture, parse_tbin,
)
from pixelheart_core.projects import new_project
from pixelheart_core.scene_preview import clear_preview_cache, resolve_scene_preview
from pixelheart_core.xnb_preview import XnbError, decode_xnb, texture_image


def i32(n):
    return struct.pack('<i', n)


def seven(n):
    result = bytearray()
    while n >= 128:
        result.append((n & 127) | 128)
        n >>= 7
    result.append(n)
    return bytes(result)


def string(text, tbin=False):
    payload = text.encode()
    return (i32(len(payload)) if tbin else seven(len(payload))) + payload


def container(reader, data, compression=None):
    body = b'\x01' + string(reader) + i32(0) + b'\x00\x01' + data
    flags = 0
    packed = body
    if compression == 'lzx':
        # A valid LZX uncompressed block, split into XNB frames while retaining
        # the block's remaining byte count across frames.
        flags = 0x80
        bitstream = '0' + '011' + f'{len(body):024b}'
        bitstream += '0' * ((16 - len(bitstream) % 16) % 16)
        header = b''.join(struct.pack('<H', int(bitstream[n:n + 16], 2)) for n in range(0, len(bitstream), 16))
        pieces = []
        for start in range(0, len(body), 32768):
            part = body[start:start + 32768]
            raw = (header + struct.pack('<3I', 1, 1, 1) if start == 0 else b'') + part
            pieces.append(b'\xff' + struct.pack('>HH', len(part), len(raw)) + raw)
        packed = i32(len(body)) + b''.join(pieces)
    elif compression == 'lz4':
        flags = 0x40
        remaining = len(body) - 15
        length = bytearray()
        while remaining >= 255:
            length.append(255)
            remaining -= 255
        length.append(remaining)
        packed = i32(len(body)) + b'\xf0' + bytes(length) + body
    return b'XNBw\x05' + bytes([flags]) + i32(10 + len(packed)) + packed


def texture(size=(16, 16), color=(20, 80, 120, 255), compression=None):
    image = Image.new('RGBA', size, color)
    data = struct.pack('<5i', 0, *size, 1, len(image.tobytes())) + image.tobytes()
    return container('Microsoft.Xna.Framework.Content.Texture2DReader', data, compression)


def tbin(image='spring_tiles', index=0, width=2, front=True):
    empty = i32(0)
    result = b'tBIN10' + string('Test', True) + string('', True) + empty
    result += i32(1) + string('sheet', True) + string('', True) + string(image, True)
    result += struct.pack('<8i', 1, 1, 16, 16, 0, 0, 0, 0) + empty
    layers = ['Back', 'Buildings', 'Front'] if front else ['Back']
    result += i32(len(layers))
    for name in layers:
        result += string(name, True) + b'\x01' + string('', True) + struct.pack('<4i', width, 1, 16, 16) + empty
        if name == 'Buildings':
            result += b'N' + i32(width)
        else:
            result += b'T' + string('sheet', True)
            result += b'S' + i32(index) + b'\x00' + empty
            if width > 1:
                result += b'N' + i32(width - 1)
    return result


def map_xnb(**kwargs):
    body = tbin(**kwargs)
    return container('xTile.Pipeline.TideReader', i32(len(body)) + body, 'lzx')


class XnbPreviewTests(unittest.TestCase):
    def test_lzx_verbatim_and_aligned_huffman_matches(self):
        from pixelheart_core.xnb_preview import _lzx
        for kind in (1, 2):
            # A compact handcrafted tree emits A..P, then a two-byte match
            # sixteen bytes back. It exercises both offset coding modes.
            bits = '0' + f'{kind:03b}' + f'{18:024b}'
            if kind == 2:
                bits += '011' * 8
            for start, end in ((0, 256), (256, 512), (0, 249)):
                bits += '0101' * 20
                for symbol in range(start, end):
                    active = (end == 256 and 65 <= symbol <= 80) or (start == 256 and symbol == 320)
                    bits += f'{12 if active else 0:05b}'
            bits += ''.join(f'{i:05b}' for i in range(16)) + '10000' + '010'
            bits += '0' * ((16 - len(bits) % 16) % 16)
            packed = b''.join(struct.pack('<H', int(bits[n:n + 16], 2)) for n in range(0, len(bits), 16))
            frame = b'\xff' + struct.pack('>HH', 18, len(packed)) + packed
            self.assertEqual(_lzx(frame, 18), b'ABCDEFGHIJKLMNOPAB')

    def test_texture_uncompressed_lzx_multiframe_and_lz4(self):
        for compression in (None, 'lzx', 'lz4'):
            with self.subTest(compression=compression):
                image = texture_image(texture((128, 128), (5, 10, 15, 255), compression))
                self.assertEqual(image.size, (128, 128))
                self.assertEqual(image.getpixel((127, 127)), (5, 10, 15, 255))

    def test_premultiplied_pixels_restore_straight_alpha(self):
        result = texture_image(texture((1, 1), (40, 20, 10, 128)))
        self.assertEqual(result.getpixel((0, 0)), (80, 40, 20, 128))

    def test_truncated_bomb_invalid_headers_and_untrusted_readers_fail(self):
        good = texture()
        malformed = [b'', good[:9], good[:-1], good.replace(b'XNB', b'BAD', 1),
                     b'XNBw\x05\x80' + i32(14) + i32(70_000_000),
                     container('Unknown.ExecuteMe', b'payload'),
                     container('Microsoft.Xna.Framework.Content.Texture2DReader', struct.pack('<5i', 0, 1000000, 1000000, 1, 0))]
        for payload in malformed:
            with self.subTest(length=len(payload)), self.assertRaises(XnbError):
                texture_image(payload)
        with self.assertRaises(XnbError):
            decode_xnb(container('x', b'x', 'lzx')[:-1])

    def test_lz4_overlap_and_malformed_distance(self):
        from pixelheart_core.xnb_preview import _lz4
        self.assertEqual(_lz4(b'\x11a\x01\x00', 6), b'aaaaaa')
        for data in (b'\x01\x00\x00', b'\x11a\x02\x00', b'\xf0\xff'):
            with self.assertRaises(XnbError):
                _lz4(data, 6)

    def test_tbin_rejects_unsafe_dimensions_invalid_tiles_and_truncation(self):
        for payload in (tbin(width=257), tbin(index=1), tbin()[:-1], b'wrong!'):
            with self.assertRaises(XnbError):
                parse_tbin(payload)


class InstalledSceneTests(unittest.TestCase):
    def setUp(self):
        clear_preview_cache()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.game = self.root / 'game'
        self.content = self.game / 'Contents/Resources/Content'
        self.write('Maps/Town.xnb', map_xnb())
        self.write('Maps/spring_tiles.xnb', texture())
        self.document = new_project()
        self.project_file = self.root / 'character.json'
        self.event = {'location': 'Town', 'story': {'season': 'spring', 'actors': [{'name': 'Abigail'}, {'name': 'farmer'}]}}

    def write(self, name, payload):
        path = self.content / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def farmer(self):
        for name, size, color in [('farmer_base', (288, 96), (240, 150, 100, 255)),
                                 ('farmer_girl_base', (288, 96), (220, 140, 90, 255)),
                                 ('pants', (192, 96), (200, 200, 200, 255)),
                                 ('shirts', (256, 32), (120, 10, 20, 255)),
                                 ('hairstyles', (128, 288), (120, 100, 70, 0)),
                                 ('skinColors', (3, 1), (240, 160, 120, 255)),
                                 ('shoeColors', (4, 3), (90, 60, 10, 255))]:
            self.write('Characters/Farmer/' + name + '.xnb', texture(size, color))

    def test_known_bundle_layout_and_no_discovery_when_explicit_root(self):
        self.assertEqual(content_root(self.game), self.content)
        self.assertEqual(content_root(self.content), self.content)
        self.assertEqual(content_root(self.game / 'Contents/Resources'), self.content)
        with patch('pixelheart_core.game_scene_assets.discover_game_root', side_effect=AssertionError('must not scan')):
            result = resolve_scene_preview(self.document, self.project_file, self.event, export_root=self.root)
        self.assertIsNone(result['background'])

    def test_actual_background_foreground_and_cache_reuse_dependency_refresh(self):
        from pixelheart_core.game_scene_assets import parse_tbin as parser
        with patch('pixelheart_core.game_scene_assets.parse_tbin', wraps=parser) as parsed:
            first = load_game_map(self.game, 'Town')
            again = load_game_map(self.game, 'Town')
            self.assertEqual(parsed.call_count, 1)
            self.assertEqual(first['map_size'], (2, 1))
            self.assertEqual(first['image'].getpixel((0, 0)), (20, 80, 120, 255))
            self.assertEqual(first['foreground'].getpixel((0, 0)), (20, 80, 120, 255))
            first['image'].putpixel((0, 0), (0, 0, 0, 0))
            self.assertEqual(again['image'].getpixel((0, 0)), (20, 80, 120, 255))
            self.write('Maps/spring_tiles.xnb', texture(color=(200, 60, 10, 255)))
            changed = load_game_map(self.game, 'Town')
            self.assertEqual(parsed.call_count, 2)
            self.assertNotEqual(again['cache_key'], changed['cache_key'])
            self.assertEqual(changed['image'].getpixel((0, 0)), (200, 60, 10, 255))

    def test_season_switches_real_tilesheets(self):
        self.write('Maps/winter_tiles.xnb', texture(color=(250, 250, 250, 255)))
        result = load_game_map(self.game, 'Town', season='winter')
        self.assertEqual(result['image'].getpixel((0, 0)), (250, 250, 250, 255))
        self.assertEqual(load_game_map(self.game, 'Town', season='fall')['image'].getpixel((0, 0)), (20, 80, 120, 255))

    def test_seasonal_fallback_cache_recovers_when_optional_asset_appears_or_disappears(self):
        first = load_game_map(self.game, 'Town', season='winter')
        self.assertEqual(first['image'].getpixel((0, 0)), (20, 80, 120, 255))
        winter = self.write('Maps/winter_tiles.xnb', texture(color=(250, 250, 250, 255)))
        appeared = load_game_map(self.game, 'Town', season='winter')
        self.assertEqual(appeared['image'].getpixel((0, 0)), (250, 250, 250, 255))
        winter.unlink()
        removed = load_game_map(self.game, 'Town', season='winter')
        self.assertEqual(removed['image'].getpixel((0, 0)), (20, 80, 120, 255))

    def test_combined_map_texture_budget_applies_beyond_global_cache_eviction(self):
        with patch('pixelheart_core.game_scene_assets._MAX_MAP_TEXTURE_PIXELS', 200):
            with self.assertRaisesRegex(XnbError, 'combined preview image limit'):
                load_game_map(self.game, 'Town')

    def test_real_npc_portrait_farmer_and_selected_project_art_coexist(self):
        self.farmer()
        self.write('Characters/Abigail.xnb', texture((64, 128), (15, 20, 25, 255), 'lzx'))
        self.write('Portraits/Abigail.xnb', texture((128, 128), (50, 60, 70, 255)))
        Image.new('RGBA', (64, 128), 'orange').save(self.root / 'selected.png')
        self.document['artwork']['sprite'] = 'selected.png'
        self.event['story']['actors'].append({'name': '$npc'})
        before = deepcopy((self.document, self.event))
        result = resolve_scene_preview(self.document, self.project_file, self.event, game_root=self.game)
        self.assertEqual(result['sprites']['farmer'].size, (64, 128))
        self.assertEqual(result['sprites']['$npc'].getpixel((0, 0)), (255, 165, 0, 255))
        self.assertEqual(result['portraits']['Abigail'].size, (128, 128))
        self.assertEqual((self.document, self.event), before)
        self.assertIn('game map', result['source_note'])
        self.assertNotIn('stand-in', result['source_note'])
        self.assertIsNotNone(result['foreground'])

    def test_export_precedence_and_installation_alias_fallback(self):
        exports = self.root / 'exports'
        exports.mkdir()
        Image.new('RGBA', (32, 16), 'yellow').save(exports / 'Maps_Town.png')
        result = resolve_scene_preview(self.document, self.project_file, self.event, export_root=exports, installation_root=self.game)
        self.assertEqual(result['background'].getpixel((0, 0)), (255, 255, 0, 255))
        self.event['location'] = 'Beach'
        self.write('Maps/Beach.xnb', map_xnb())
        result = resolve_scene_preview(self.document, self.project_file, self.event, export_root=exports, installation_root=self.game)
        self.assertEqual(result['background'].getpixel((0, 0)), (20, 80, 120, 255))

    def test_empty_export_preference_still_loads_installed_npc_art(self):
        self.write('Characters/Abigail.xnb', texture((64, 128)))
        self.write('Portraits/Abigail.xnb', texture((128, 128)))
        result = resolve_scene_preview(self.document, self.project_file, self.event, export_root='', game_root=self.game)
        self.assertIn('Abigail', result['sprites'])
        self.assertIn('Abigail', result['portraits'])

    def test_invalid_export_sheet_uses_game_variant_but_selected_project_art_does_not(self):
        self.write('Characters/Abigail.xnb', texture((64, 128), (10, 20, 30, 255)))
        winter = self.write('Characters/Abigail_Winter.xnb', texture((64, 128), (240, 240, 240, 255)))
        self.write('Portraits/Abigail.xnb', texture((128, 128)))
        exports = self.root / 'exports'
        exports.mkdir()
        Image.new('RGBA', (32, 32), 'red').save(exports / 'Characters_Abigail.png')
        self.event['story']['season'] = 'winter'
        result = resolve_scene_preview(self.document, self.project_file, self.event, export_root=exports, game_root=self.game)
        self.assertEqual(result['sprites']['Abigail'].getpixel((0, 0)), (240, 240, 240, 255))
        winter.unlink()
        result = resolve_scene_preview(self.document, self.project_file, self.event, export_root=exports, game_root=self.game)
        self.assertEqual(result['sprites']['Abigail'].getpixel((0, 0)), (10, 20, 30, 255))
        self.document['character']['internal_name'] = 'Abigail'
        self.document['artwork']['sprite'] = 'exports/Characters_Abigail.png'
        result = resolve_scene_preview(self.document, self.project_file, self.event, export_root=exports, game_root=self.game)
        self.assertNotIn('Abigail', result['sprites'])

    def test_missing_or_bad_farmer_never_returns_unclothed_body(self):
        self.write('Characters/Farmer/farmer_girl_base.xnb', texture((288, 672)))
        result = resolve_scene_preview(self.document, self.project_file, self.event, game_root=self.game)
        self.assertNotIn('farmer', result['sprites'])
        self.assertIn('artwork unavailable', result['source_note'])
        self.assertNotIn('stand-in', result['source_note'])

    def test_safe_asset_containment_and_symlink_rejection(self):
        for name in ('../outside', '/outside', 'Maps/../../outside', 'Maps//Town'):
            with self.assertRaises(ValueError):
                asset_path(self.content, name)
        path = self.content / 'Maps/linked.xnb'
        path.symlink_to(self.content / 'Maps/spring_tiles.xnb')
        with self.assertRaises(ValueError):
            load_texture(self.content, 'Maps/linked')
        self.write('Maps/Town.xnb', map_xnb(image='../outside'))
        with self.assertRaises(ValueError):
            load_game_map(self.game, 'Town')

    def test_farmer_caches_and_invalidates_every_layer(self):
        self.farmer()
        from pixelheart_core.game_scene_assets import compose_farmer as compositor
        with patch('pixelheart_core.game_scene_assets.compose_farmer', wraps=compositor) as compose:
            image, _, key = load_farmer(self.game, 'female')
            again, _, key2 = load_farmer(self.game, 'female')
            self.assertEqual(compose.call_count, 1)
            self.assertEqual(key, key2)
            self.write('Characters/Farmer/shirts.xnb', texture((256, 32), (40, 80, 90, 255)))
            changed, _, key3 = load_farmer(self.game, 'female')
            self.assertEqual(compose.call_count, 2)
            self.assertNotEqual(key2, key3)
            self.assertNotEqual(image.tobytes(), changed.tobytes())


if __name__ == '__main__':
    unittest.main()
