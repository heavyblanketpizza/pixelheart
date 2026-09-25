using System.Text;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Newtonsoft.Json;
using StardewModdingAPI;
using xTile;
using xTile.Tiles;

namespace Pixelheart.Interiors;

/// <summary>Exports preview-only farmhouse framing from the installed game.</summary>
internal static class SpouseContextExporter
{
    private const int Width = 144;
    private const int Height = 176;
    private static readonly Rectangle Source = new(48, 19, 9, 11);
    private static readonly Rectangle Insert = new(2, 1, 6, 9);

    internal static Dictionary<string, object?>? Export(IModHelper helper, string output,
        List<string> warnings, ref long textureBytes, ref long jsonBytes)
    {
        var created = new List<string>();
        try
        {
            Map map = helper.GameContent.Load<Map>("Maps/FarmHouse2_marriage");
            var back = map.GetLayer("Back");
            if (back == null || back.LayerWidth != 70 || back.LayerHeight != 46)
                throw new InvalidDataException("The farmhouse source layout has changed.");
            var textures = new Dictionary<string, (Texture2D Texture, Color[] Pixels)>(StringComparer.Ordinal);
            var background = new Color[Width * Height];
            var foreground = new Color[Width * Height];
            GraphicsDevice? graphics = null;
            foreach (string name in new[] { "Back", "Buildings", "Front" })
            {
                var layer = map.GetLayer(name);
                if (layer == null || layer.LayerWidth != back.LayerWidth || layer.LayerHeight != back.LayerHeight)
                    throw new InvalidDataException("The farmhouse source layers have changed.");
                for (int y = 0; y < Source.Height; y++)
                    for (int x = 0; x < Source.Width; x++)
                    {
                        bool inside = Insert.Contains(x, y);
                        // The farmhouse owns the lower Front trim. Preserve it
                        // above the authored insert, without copying any of the
                        // source room's furniture, floor, wallpaper or actions.
                        bool lowerTrim = inside && name == "Front" && y == Insert.Bottom - 1;
                        if (inside && !lowerTrim) continue;
                        Tile? sourceTile = layer.Tiles[Source.X + x, Source.Y + y];
                        if (sourceTile == null) continue;
                        if (sourceTile is not StaticTile tile || tile.BlendMode != BlendMode.Alpha)
                            throw new InvalidDataException("The farmhouse context contains unsupported animated or blended tiles.");
                        string asset = tile.TileSheet.ImageSource.Replace('\\', '/');
                        if (asset.EndsWith(".png", StringComparison.OrdinalIgnoreCase)) asset = asset[..^4];
                        if (!asset.StartsWith("Maps/", StringComparison.Ordinal)) asset = "Maps/" + asset;
                        if (asset.Split('/').Any(part => string.IsNullOrWhiteSpace(part) || part is "." or "..")
                            || asset.Contains(':') || asset.Any(char.IsControl))
                            throw new InvalidDataException("The farmhouse tilesheet asset name is unsupported.");
                        if (!textures.TryGetValue(asset, out var source))
                        {
                            Texture2D texture = helper.GameContent.Load<Texture2D>(asset);
                            if (texture.Width < 16 || texture.Height < 16 || texture.Width % 16 != 0
                                || texture.Height % 16 != 0 || (long)texture.Width * texture.Height > 16_777_216)
                                throw new InvalidDataException("The farmhouse source texture is unsupported.");
                            var pixels = new Color[texture.Width * texture.Height];
                            texture.GetData(pixels);
                            source = (texture, pixels);
                            textures.Add(asset, source);
                            graphics ??= texture.GraphicsDevice;
                        }
                        if (tile.TileSheet.TileWidth != 16 || tile.TileSheet.TileHeight != 16
                            || tile.TileSheet.MarginWidth != 0 || tile.TileSheet.MarginHeight != 0
                            || tile.TileSheet.SpacingWidth != 0 || tile.TileSheet.SpacingHeight != 0
                            || tile.TileSheet.SheetWidth != source.Texture.Width / 16
                            || tile.TileSheet.SheetHeight > source.Texture.Height / 16
                            || tile.TileIndex < 0 || tile.TileIndex >= tile.TileSheet.SheetWidth * tile.TileSheet.SheetHeight)
                            throw new InvalidDataException("The farmhouse tilesheet geometry has changed.");
                        int sx = tile.TileIndex % tile.TileSheet.SheetWidth * 16;
                        int sy = tile.TileIndex / tile.TileSheet.SheetWidth * 16;
                        Color[] target = lowerTrim ? foreground : background;
                        for (int py = 0; py < 16; py++)
                            for (int px = 0; px < 16; px++)
                            {
                                int destination = (y * 16 + py) * Width + x * 16 + px;
                                target[destination] = Composite(source.Pixels[(sy + py) * source.Texture.Width + sx + px], target[destination]);
                            }
                    }
            }
            if (graphics == null) throw new InvalidDataException("The farmhouse context has no drawable artwork.");
            string textureDirectory = Path.Combine(output, "textures");
            if (Directory.EnumerateFiles(textureDirectory).Take(511).Count() > 510)
                throw new InvalidDataException("The library reached its texture count limit.");
            var context = new Dictionary<string, object?>
            {
                ["background_asset"] = "textures/spouse-context-background.png",
                ["foreground_asset"] = "textures/spouse-context-foreground.png"
            };
            long metadataBytes = Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(context)) + 32;
            if (jsonBytes + metadataBytes > 8 * 1024 * 1024 - 128 * 1024)
                throw new InvalidDataException("The library reached its context metadata limit.");
            // Encode both layers before publishing either file, so a missing
            // source, unsupported map or size limit only omits this context.
            byte[] backgroundPng = Encode(graphics, background);
            byte[] foregroundPng = Encode(graphics, foreground);
            long pngBytes = backgroundPng.LongLength + foregroundPng.LongLength;
            if (backgroundPng.LongLength > 16 * 1024 * 1024 || foregroundPng.LongLength > 16 * 1024 * 1024
                || textureBytes + pngBytes > 128 * 1024 * 1024)
                throw new InvalidDataException("The farmhouse context exceeds the library's PNG size limit.");
            foreach (var (key, bytes) in new[] { ("background_asset", backgroundPng), ("foreground_asset", foregroundPng) })
            {
                string path = Path.Combine(output, (string)context[key]!);
                using var target = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                created.Add(path);
                target.Write(bytes, 0, bytes.Length);
            }
            textureBytes += pngBytes;
            jsonBytes += metadataBytes;
            return context;
        }
        catch (Exception ex)
        {
            foreach (string path in created)
            {
                try { File.Delete(path); }
                catch { /* Preserve a locked generated file without aborting the library. */ }
            }
            if (warnings.Count < 1000)
            {
                string message = new(ex.Message.Where(character => !char.IsControl(character)).Take(400).ToArray());
                warnings.Add($"Spouse room farmhouse preview unavailable ({message}).");
            }
            return null;
        }
    }

    private static Color Composite(Color source, Color destination)
    {
        // XNB texture colors use the game's premultiplied AlphaBlend convention.
        int inverse = 255 - source.A;
        return new Color(Math.Min(255, source.R + (destination.R * inverse + 127) / 255),
            Math.Min(255, source.G + (destination.G * inverse + 127) / 255),
            Math.Min(255, source.B + (destination.B * inverse + 127) / 255),
            Math.Min(255, source.A + (destination.A * inverse + 127) / 255));
    }

    private static byte[] Encode(GraphicsDevice graphics, Color[] pixels)
    {
        // MonoGame's SaveAsPng writes GetColorData directly. PNG/PIL expects
        // straight RGBA, so undo premultiplication after all source-over draws.
        // See MonoGame.Framework/Platform/Graphics/Texture2D.StbSharp.cs.
        var straight = new Color[pixels.Length];
        for (int index = 0; index < pixels.Length; index++)
        {
            Color color = pixels[index];
            straight[index] = color.A == 0 ? Color.Transparent : color.A == 255 ? color
                : new Color(Math.Min(255, (color.R * 255 + color.A / 2) / color.A),
                    Math.Min(255, (color.G * 255 + color.A / 2) / color.A),
                    Math.Min(255, (color.B * 255 + color.A / 2) / color.A), color.A);
        }
        using var preview = new Texture2D(graphics, Width, Height);
        preview.SetData(straight);
        using var encoded = new MemoryStream();
        preview.SaveAsPng(encoded, Width, Height);
        return encoded.ToArray();
    }
}
