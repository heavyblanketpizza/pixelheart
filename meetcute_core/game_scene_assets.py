"""Actual scene assets read directly from an installed Stardew Valley game.

Only known installation locations and map-referenced assets are inspected. No
files are written, no code from the installation is loaded, and no game assets
are included in the application. Cached results are bounded and keyed by stat.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import re
from PIL import Image, ImageChops

from .local_templates import _safe_path, _read_bounded
from .xnb_preview import Reader, XnbError, texture_image, map_payload, MAX_PACKED

_CACHE = OrderedDict()
_MAX_CACHE_PIXELS = 24_000_000
_MAX_MAP_TEXTURE_PIXELS = 16_777_216
_MAX_CACHED_TILES = 4096
_ASSET = re.compile(r'[A-Za-z0-9_. /-]{1,256}\Z')


def clear_game_cache():
    _CACHE.clear()


def _remember(key, record):
    _CACHE[key] = record
    _CACHE.move_to_end(key)
    while len(_CACHE) > 40 or sum(im.width * im.height for row in _CACHE.values()
                                for im in row.get('images', ())) > _MAX_CACHE_PIXELS:
        _CACHE.popitem(last=False)


def content_root(folder):
    """Find Content below one supplied installation or macOS bundle only."""
    if not folder:
        raise XnbError('Locate your Stardew Valley installation to show game artwork.')
    root = Path(folder).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise XnbError('Choose the Stardew Valley game folder.')
    candidates = ('', 'Content', 'Contents/Resources/Content', 'Contents/MacOS/Content',
                  'Stardew Valley.app/Contents/Resources/Content', 'Stardew Valley.app/Contents/MacOS/Content')
    for relative in candidates:
        candidate = root if not relative else _safe_path(root, relative, directory=True)
        if candidate is not None and _safe_path(candidate, 'Maps/Town.xnb') is not None:
            return candidate
    raise XnbError('This folder does not contain Stardew Valley game Content.')


def discover_game_root():
    """Return the first installed game at a known location, or None.

    This opt-in helper does not inspect exports or recursively scan directories.
    Callers should save the chosen installation in their existing preferences.
    """
    from .game_install import find_games
    found = find_games(limit=1)
    return found[0].root if found else None


def asset_path(root, asset):
    asset = str(asset).replace('\\', '/')
    if not _ASSET.fullmatch(asset) or any(part in ('', '.', '..') for part in asset.split('/')):
        raise XnbError('The game asset reference is not a safe local path.')
    if asset.lower().endswith(('.png', '.xnb')):
        asset = asset[:-4]
    path = _safe_path(root, asset + '.xnb')
    if path is None:
        raise XnbError(f'Game asset {asset} is unavailable.')
    return path


def stat_key(root, path):
    checked = _safe_path(root, path.relative_to(root))
    if checked is None:
        raise XnbError('A game asset is unavailable.')
    s = checked.stat()
    return (str(checked), s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino)


def _dependency_key(root, path):
    try:
        return stat_key(root, path)
    except (OSError, ValueError, RuntimeError):
        return (str(path), 'unavailable')


def load_texture(root, asset):
    path = asset_path(root, asset)
    key = ('texture', stat_key(root, path))
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached['images'][0].copy(), path, key
    image = texture_image(_read_bounded(root, path, MAX_PACKED, None))
    if key[1] != stat_key(root, path):
        raise XnbError('The game asset changed while loading; please retry.')
    _remember(key, {'images': (image,)})
    return image.copy(), path, key


def _properties(reader):
    result = {}
    for _ in range(reader.count(8192)):
        name, kind = reader.string(tbin=True), reader.byte()
        if kind == 0:
            value = bool(reader.byte())
        elif kind == 1:
            value = reader.number('i')
        elif kind == 2:
            value = reader.number('f')
        elif kind == 3:
            value = reader.string(tbin=True)
        else:
            raise XnbError('The map has an unknown property type.')
        result[name] = value
    return result


def parse_tbin(payload):
    """Parse bounded tBIN10 layout data, preserving the first animation frame."""
    reader = Reader(payload)
    if reader.take(6) != b'tBIN10':
        raise XnbError('The game map has an unsupported tile format.')
    name, description = reader.string(tbin=True), reader.string(tbin=True)
    props = _properties(reader)
    sheets = {}
    for _ in range(reader.count(64)):
        sheet_id, description, image = [reader.string(tbin=True) for _ in range(3)]
        size, tile, margin, spacing = [tuple(reader.number('i') for _ in range(2)) for _ in range(4)]
        if (not all(0 < v <= 4096 for v in size) or tile != (16, 16)
                or not all(0 <= v <= 256 for v in margin + spacing) or sheet_id in sheets):
            raise XnbError('The map tilesheet has unsupported dimensions.')
        sheets[sheet_id] = dict(image=image, size=size, tile=tile, margin=margin, spacing=spacing,
                                properties=_properties(reader))
    layers = []
    budget = 0
    def static(sheet):
        index, blend = reader.number('i'), reader.byte()
        values = _properties(reader)
        if sheet not in sheets or not 0 <= index < sheets[sheet]['size'][0] * sheets[sheet]['size'][1] or blend not in (0, 1):
            raise XnbError('The game map has an invalid tile reference.')
        return sheet, index, blend, values
    for _ in range(reader.count(32)):
        layer_name, visible = reader.string(tbin=True), bool(reader.byte())
        reader.string(tbin=True)
        size = tuple(reader.number('i') for _ in range(2))
        tile = tuple(reader.number('i') for _ in range(2))
        if not all(0 < v <= 256 for v in size) or tile != (16, 16):
            raise XnbError('The game map is outside the preview dimensions.')
        budget += size[0] * size[1]
        if budget > 524288:
            raise XnbError('The game map contains too many tiles.')
        properties = _properties(reader)
        entries, sheet = [], ''
        for y in range(size[1]):
            x = 0
            while x < size[0]:
                command = reader.byte()
                if command == ord('T'):
                    sheet = reader.string(tbin=True)
                    if sheet not in sheets:
                        raise XnbError('The map refers to a missing tilesheet.')
                elif command == ord('N'):
                    skip = reader.count(size[0] - x)
                    if skip == 0:
                        raise XnbError('The map has an empty tile run.')
                    x += skip
                elif command in (ord('S'), ord('A')):
                    if command == ord('S'):
                        value = static(sheet)
                    else:
                        reader.count(86_400_000)
                        frames, frame_sheet = [], ''
                        count = reader.count(256)
                        if count == 0:
                            raise XnbError('The map has an empty animation.')
                        while len(frames) < count:
                            code = reader.byte()
                            if code == ord('T'):
                                frame_sheet = reader.string(tbin=True)
                            elif code == ord('S'):
                                frames.append(static(frame_sheet))
                            else:
                                raise XnbError('The map has an invalid animation frame.')
                        _properties(reader)
                        value = frames[0]
                    entries.append((x, y, value))
                    x += 1
                else:
                    raise XnbError('The map has an unsupported tile command.')
        layers.append(dict(name=layer_name, visible=visible, size=size, properties=properties, tiles=entries))
    if not layers or reader.pos != len(reader.data):
        raise XnbError('The game map has an incomplete or unexpected payload.')
    return dict(name=name, properties=props, sheets=sheets, layers=layers)


def load_game_map(folder, location, *, season='spring', maximum=2048):
    """Render actual map Back/Buildings and Front/AlwaysFront into two images."""
    if not isinstance(location, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,191}', location):
        raise XnbError('Choose a supported game location.')
    if type(maximum) is not int or not 1 <= maximum <= 4096 or season not in ('spring', 'summer', 'fall', 'winter'):
        raise XnbError('Choose a supported map season and preview size.')
    root = content_root(folder)
    path = asset_path(root, 'Maps/' + location)
    identity = ('map', str(root), location, season, maximum)
    old = _CACHE.get(identity)
    if old is not None and all(_dependency_key(root, Path(key[0])) == key for key in old['dependencies']):
        _CACHE.move_to_end(identity)
        back, front = old['images']
        return dict(image=back.copy(), foreground=front.copy(), map_size=old['size'], path=path,
                    cache_key=(identity, old['dependencies']), source='Stardew Valley game map')
    map_key = stat_key(root, path)
    data = parse_tbin(map_payload(_read_bounded(root, path, MAX_PACKED, None)))
    dependencies, textures, unique_textures = [map_key], {}, {}
    texture_pixels = 0
    for sheet_id, sheet in data['sheets'].items():
        image = sheet['image'].replace('\\', '/')
        if image.startswith('Maps/'):
            image = image[5:]
        image = image.rsplit('.', 1)[0] if image.lower().endswith(('.png', '.xnb')) else image
        if '/' in image:
            raise XnbError('The game map has an unsupported tilesheet path.')
        if season in ('summer', 'fall', 'winter') and image.startswith('spring_'):
            seasonal = season + image[6:]
            dependencies.append(_dependency_key(root, root / ('Maps/' + seasonal + '.xnb')))
            try:
                asset_path(root, 'Maps/' + seasonal)
                image = seasonal
            except XnbError:
                pass
        if image in unique_textures:
            texture = unique_textures[image]
        else:
            texture, texture_path, key = load_texture(root, 'Maps/' + image)
            texture_pixels += texture.width * texture.height
            if texture_pixels > _MAX_MAP_TEXTURE_PIXELS:
                texture.close()
                raise XnbError('The map tilesheets exceed the combined preview image limit.')
            unique_textures[image] = texture
            dependencies.append(key[1])
        textures[sheet_id] = texture
    width = max(layer['size'][0] for layer in data['layers'])
    height = max(layer['size'][1] for layer in data['layers'])
    back = Image.new('RGBA', (width * 16, height * 16))
    front = Image.new('RGBA', back.size)
    tiles = OrderedDict()
    for layer in data['layers']:
        if not layer['visible'] or layer['name'].lower() in ('paths', 'paths2'):
            continue
        target = front if layer['name'].lower().startswith(('front', 'alwaysfront')) else back
        for x, y, (sheet_id, index, blend, properties) in layer['tiles']:
            tile_key = (sheet_id, index)
            tile = tiles.get(tile_key)
            if tile is None:
                sheet, texture = data['sheets'][sheet_id], textures[sheet_id]
                sx = sheet['margin'][0] + index % sheet['size'][0] * (16 + sheet['spacing'][0])
                sy = sheet['margin'][1] + index // sheet['size'][0] * (16 + sheet['spacing'][1])
                if sx + 16 > texture.width or sy + 16 > texture.height:
                    raise XnbError('A game map tile falls outside its tilesheet image.')
                tile = texture.crop((sx, sy, sx + 16, sy + 16))
                tiles[tile_key] = tile
                if len(tiles) > _MAX_CACHED_TILES:
                    tiles.popitem(last=False)
            else:
                tiles.move_to_end(tile_key)
            if blend == 1:
                region = target.crop((x * 16, y * 16, x * 16 + 16, y * 16 + 16))
                target.paste(ImageChops.add(region, tile), (x * 16, y * 16))
            else:
                target.alpha_composite(tile, (x * 16, y * 16))
    if max(back.size) > maximum:
        back.thumbnail((maximum, maximum), Image.Resampling.NEAREST)
        front = front.resize(back.size, Image.Resampling.NEAREST)
    dependencies = tuple(dependencies)
    if any(_dependency_key(root, Path(key[0])) != key for key in dependencies):
        raise XnbError('The game map changed while loading; please retry.')
    _remember(identity, {'images': (back, front), 'dependencies': dependencies, 'size': (width, height)})
    return dict(image=back.copy(), foreground=front.copy(), map_size=(width, height), path=path,
                cache_key=(identity, dependencies), source='Stardew Valley game map')


def _tint(image, color):
    """Apply XNA's component-wise clothing/hair tint, preserving alpha."""
    channels = image.split()
    return Image.merge('RGBA', tuple(channels[i].point([v * color[i] // 255 for v in range(256)])
                                    for i in range(3)) + (channels[3],))


def compose_farmer(layers, gender='female'):
    """Compose a simple clothed preview from real farmer layers, in NPC layout.

    Uses shirt/pants 0, skin palette 0, brown boots/hair (style 0 or 16), and
    blue trousers. This is a preview appearance, not a save-file character.
    Animation is limited to the four standard walking/idle directions.
    """
    female = gender == 'female'
    body, pants, shirts, hair = (layers[n] for n in ('body', 'pants', 'shirts', 'hair'))
    skin, shoes = layers['skin'], layers['shoes']
    minimums = ((body, (278, 96)), (pants, (192, 96)), (shirts, (256, 32)),
                (hair, (128, 288 if female else 96)), (skin, (3, 1)), (shoes, (4, 3)))
    if any(im.width < size[0] or im.height < size[1] for im, size in minimums):
        raise XnbError('The local farmer layers have unsupported dimensions.')
    palette = {body.getpixel((260 + i, 0)): skin.getpixel((i, 0)) for i in range(3)}
    palette.update({body.getpixel((268 + i, 0)): shoes.getpixel((i, 2)) for i in range(4)})
    for i in range(3):
        shade = shirts.getpixel((128, 4 - i))
        if shade[3] != 255:
            shade = shirts.getpixel((0, 4 - i))
        palette[body.getpixel((256 + i, 0))] = shade
    palette[body.getpixel((276, 0))] = (70, 105, 145, 255)
    palette[body.getpixel((277, 0))] = (40, 65, 105, 255)
    # Restrict palette work to the walking/idle region, leaving source sheets
    # untouched. Palette matching uses original colours before substitutions.
    source = body.crop((0, 0, 192, 96))
    source.putdata([palette.get(pixel, pixel) if pixel[3] else pixel for pixel in source.get_flattened_data()])
    result = Image.new('RGBA', (64, 128))
    hair_index = 16 if female else 0
    hx = hair_index * 16 % hair.width
    hy = hair_index * 16 // hair.width * 96
    for row in range(4):
        source_row = row if row < 3 else 1
        facing = (2, 1, 0, 3)[row]
        for column, frame in enumerate((0, 1, 0, 2)):
            y = source_row * 32
            x = frame * 16
            feature_y = (0 if source_row == 2 else 1) + (1 if frame else 0)
            flip = row == 3
            torso = source.crop((x, y, x + 16, y + 32))
            arms = source.crop((96 + x, y, 112 + x, y + 32))
            px = (96 if female else 0) + x
            trousers = _tint(pants.crop((px, y, px + 16, y + 32)), (60, 90, 150))
            if flip:
                torso = torso.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                arms = arms.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                trousers = trousers.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            target = Image.new('RGBA', (16, 32))
            if facing == 0:
                target.alpha_composite(arms)
            target.alpha_composite(torso)
            target.alpha_composite(trousers)
            sy = {2: 0, 1: 8, 3: 16, 0: 24}[facing]
            shirt = shirts.crop((0, sy, 8, sy + 8))
            shirt.alpha_composite(shirts.crop((128, sy, 136, sy + 8)))
            target.alpha_composite(shirt, (4, 14 + feature_y))
            hairstyle = _tint(hair.crop((hx, hy + source_row * 32, hx + 16, hy + source_row * 32 + 32)), (120, 80, 45))
            if flip:
                hairstyle = hairstyle.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            target.alpha_composite(hairstyle, (0, feature_y + (1 if facing == 0 else 0)))
            if facing != 0:
                target.alpha_composite(arms)
            result.alpha_composite(target, (column * 16, row * 32))
    return result


def load_farmer(folder, gender='female'):
    """Return a clothed standard sprite sheet made only from local game layers."""
    gender = str(gender).lower()
    if gender not in ('female', 'male'):
        raise XnbError('Choose Woman or Man for the farmer preview.')
    root = content_root(folder)
    names = {'body': 'farmer_girl_base' if gender == 'female' else 'farmer_base',
             'pants': 'pants', 'shirts': 'shirts', 'hair': 'hairstyles',
             'skin': 'skinColors', 'shoes': 'shoeColors'}
    paths = {kind: asset_path(root, 'Characters/Farmer/' + name) for kind, name in names.items()}
    dependencies = tuple(stat_key(root, path) for path in paths.values())
    key = ('farmer', gender, dependencies)
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached['images'][0].copy(), paths['body'], key
    layers = {kind: load_texture(root, 'Characters/Farmer/' + name)[0] for kind, name in names.items()}
    sheet = compose_farmer(layers, gender)
    if dependencies != tuple(stat_key(root, path) for path in paths.values()):
        raise XnbError('The farmer artwork changed while loading; please retry.')
    _remember(key, {'images': (sheet,)})
    return sheet.copy(), paths['body'], key
