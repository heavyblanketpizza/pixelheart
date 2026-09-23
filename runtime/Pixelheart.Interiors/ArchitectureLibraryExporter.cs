using System.Text;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Newtonsoft.Json;
using StardewModdingAPI;
using xTile;
using xTile.Tiles;

namespace Pixelheart.Interiors;

/// <summary>Copies verified static architectural tiles from the installed game.</summary>
internal static class ArchitectureLibraryExporter
{
    private sealed class Recipes
    {
        public int Version { get; set; }
        [JsonProperty("recipes")] public List<Recipe> Items { get; set; } = new();
    }

    private sealed class Recipe
    {
        public string Id { get; set; } = "";
        public string Name { get; set; } = "";
        public string Category { get; set; } = "";
        public string Placement { get; set; } = "";
        public string Rules { get; set; } = "";
        public string Map { get; set; } = "";
        [JsonProperty("map_size")] public int[] MapSize { get; set; } = Array.Empty<int>();
        public string Texture { get; set; } = "";
        public int Width { get; set; }
        public int Height { get; set; }
        public List<Cell> Cells { get; set; } = new();
    }

    private sealed class Cell
    {
        public string Layer { get; set; } = "";
        public int X { get; set; }
        public int Y { get; set; }
        [JsonProperty("source_x")] public int SourceX { get; set; }
        [JsonProperty("source_y")] public int SourceY { get; set; }
        public int Tile { get; set; }
    }

    internal static List<Dictionary<string, object?>> Export(IModHelper helper, string output,
        List<string> warnings, ref long textureBytes, ref long jsonBytes)
    {
        var result = new List<Dictionary<string, object?>>();
        using Stream? resource = typeof(ArchitectureLibraryExporter).Assembly
            .GetManifestResourceStream("Pixelheart.Interiors.ArchitectureRecipes.json");
        if (resource == null) throw new InvalidDataException("The architectural library definitions are missing.");
        using var reader = new StreamReader(resource);
        var recipes = JsonConvert.DeserializeObject<Recipes>(reader.ReadToEnd());
        if (recipes?.Version != 1 || recipes.Items.Count > 128)
            throw new InvalidDataException("The architectural library definitions are unsupported.");
        var maps = new Dictionary<string, Map>(StringComparer.Ordinal);
        var textures = new Dictionary<string, (Texture2D Texture, Color[] Pixels)>(StringComparer.Ordinal);
        var atlas = new List<Color[]>();
        var tileLookup = new Dictionary<string, int>(StringComparer.Ordinal);
        GraphicsDevice? graphics = null;
        foreach (Recipe recipe in recipes.Items)
        {
            try
            {
                if (!maps.TryGetValue(recipe.Map, out Map? map))
                    maps[recipe.Map] = map = helper.GameContent.Load<Map>(recipe.Map);
                var back = map.GetLayer("Back");
                if (recipe.MapSize.Length != 2 || back == null
                    || back.LayerWidth != recipe.MapSize[0] || back.LayerHeight != recipe.MapSize[1])
                    throw new InvalidDataException("The source map layout has changed.");
                if (!textures.TryGetValue(recipe.Texture, out var source))
                {
                    Texture2D texture = helper.GameContent.Load<Texture2D>(recipe.Texture);
                    if (texture.Width < 16 || texture.Height < 16 || texture.Width % 16 != 0
                        || texture.Height % 16 != 0 || (long)texture.Width * texture.Height > 16_777_216)
                        throw new InvalidDataException("The source architectural texture is unsupported.");
                    var pixels = new Color[texture.Width * texture.Height];
                    texture.GetData(pixels);
                    source = (texture, pixels);
                    textures.Add(recipe.Texture, source);
                    graphics ??= texture.GraphicsDevice;
                }
                // Preflight the entire piece before adding anything to its atlas.
                // Source map actions, warps, lighting and animation are deliberately
                // not transplanted into another NPC's residence.
                foreach (Cell cell in recipe.Cells)
                {
                    var layer = map.GetLayer(cell.Layer);
                    if (layer == null || cell.SourceX < 0 || cell.SourceY < 0
                        || cell.SourceX >= layer.LayerWidth || cell.SourceY >= layer.LayerHeight
                        || layer.Tiles[cell.SourceX, cell.SourceY] is not StaticTile tile
                        || tile.TileIndex != cell.Tile || tile.BlendMode != BlendMode.Alpha
                        || cell.X < 0 || cell.Y < 0 || cell.X >= recipe.Width || cell.Y >= recipe.Height
                        || !new[] { "Back", "Buildings", "Front" }.Contains(cell.Layer))
                        throw new InvalidDataException("The source architectural tiles have changed.");
                    string sheet = tile.TileSheet.ImageSource.Replace('\\', '/');
                    if (sheet.StartsWith("Maps/", StringComparison.Ordinal)) sheet = sheet[5..];
                    if (sheet.EndsWith(".png", StringComparison.OrdinalIgnoreCase)) sheet = sheet[..^4];
                    if ("Maps/" + sheet != recipe.Texture
                        || tile.TileSheet.SheetWidth != source.Texture.Width / 16
                        || tile.TileSheet.SheetHeight != source.Texture.Height / 16
                        || cell.Tile < 0 || cell.Tile >= source.Pixels.Length / 256)
                        throw new InvalidDataException("The source architectural tilesheet has changed.");
                    // Properties may alter collision or draw order independently of
                    // the source layer. Only the explicitly discarded native text
                    // actions are supported by these verified static recipes.
                    if (tile.Properties.Keys.Any(key => key != "Action")
                        || tile.TileSheet.TileIndexProperties[cell.Tile].Count != 0
                        || tile.TileSheet.Properties.Count != 0 || layer.Properties.Count != 0)
                        throw new InvalidDataException("The source tiles have unsupported collision or drawing properties.");
                }
                var layers = new Dictionary<string, int?[]>();
                foreach (string name in new[] { "Back", "Buildings", "Front" })
                    layers[name] = new int?[recipe.Width * recipe.Height];
                foreach (Cell cell in recipe.Cells)
                {
                    string key = recipe.Texture + ":" + cell.Tile;
                    if (!tileLookup.TryGetValue(key, out int index))
                    {
                        if (atlas.Count >= 4096) throw new InvalidDataException("The architectural atlas is full.");
                        index = atlas.Count;
                        int sx = cell.Tile % (source.Texture.Width / 16) * 16;
                        int sy = cell.Tile / (source.Texture.Width / 16) * 16;
                        var pixels = new Color[256];
                        for (int y = 0; y < 16; y++)
                            Array.Copy(source.Pixels, (sy + y) * source.Texture.Width + sx, pixels, y * 16, 16);
                        atlas.Add(pixels);
                        tileLookup.Add(key, index);
                    }
                    layers[cell.Layer][cell.Y * recipe.Width + cell.X] = index;
                }
                var definition = new Dictionary<string, object?>
                {
                    ["id"] = recipe.Id, ["name"] = recipe.Name, ["category"] = recipe.Category,
                    ["placement"] = recipe.Placement, ["width"] = recipe.Width, ["height"] = recipe.Height,
                    ["rules"] = recipe.Rules,
                    ["layers"] = layers, ["preview_asset"] = "textures/architecture.png", ["columns"] = 16
                };
                long bytes = Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(definition)) + 32;
                if (jsonBytes + bytes > 8 * 1024 * 1024 - 128 * 1024)
                    throw new InvalidDataException("The library reached its architectural metadata limit.");
                jsonBytes += bytes;
                result.Add(definition);
            }
            catch (Exception ex)
            {
                if (warnings.Count < 1000)
                {
                    string message = new(ex.Message.Where(character => !char.IsControl(character)).Take(400).ToArray());
                    warnings.Add($"{recipe.Name}: architectural piece unavailable ({message}).");
                }
            }
        }
        if (result.Count == 0) return result;
        if (graphics == null || Directory.EnumerateFiles(Path.Combine(output, "textures")).Take(512).Count() >= 512)
            throw new InvalidDataException("The library reached its texture count limit.");
        int rows = (atlas.Count + 15) / 16;
        var combined = new Color[256 * rows * 16];
        for (int index = 0; index < atlas.Count; index++)
            for (int y = 0; y < 16; y++)
                Array.Copy(atlas[index], y * 16, combined, (index / 16 * 16 + y) * 256 + index % 16 * 16, 16);
        using var preview = new Texture2D(graphics, 256, rows * 16);
        preview.SetData(combined);
        using var encoded = new MemoryStream();
        preview.SaveAsPng(encoded, preview.Width, preview.Height);
        if (encoded.Length > 16 * 1024 * 1024 || textureBytes + encoded.Length > 128 * 1024 * 1024)
            throw new InvalidDataException("The architectural texture exceeds the library's PNG size limit.");
        foreach (var piece in result) piece["tile_count"] = rows * 16;
        using var target = new FileStream(Path.Combine(output, "textures", "architecture.png"),
            FileMode.CreateNew, FileAccess.Write, FileShare.None);
        encoded.Position = 0;
        encoded.CopyTo(target);
        textureBytes += encoded.Length;
        return result;
    }
}
