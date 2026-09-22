using System.Globalization;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Newtonsoft.Json;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Objects;

namespace Pixelheart.Interiors;

/// <summary>Exports installed item observations into Pixelheart's own portable preview format.</summary>
internal static class FurnitureLibraryExporter
{
    private const int MaxItems = 10_000;
    private const int MaxSurfaces = 2048;
    private const int MaxSheets = 512;
    private const long MaxPngBytes = 16 * 1024 * 1024;
    private const long MaxTotalPngBytes = 128 * 1024 * 1024;
    private const int MaxJsonBytes = 8 * 1024 * 1024;
    internal sealed record Result(string Path, int Count, int WarningCount);
    private sealed record Sheet(string Path, int OriginalWidth, int Height);
    private sealed class CacheOwnership
    {
        public int Version { get; set; } = 1;
        public Dictionary<string, string> Files { get; set; } = new(StringComparer.Ordinal);
    }

    internal static Result Export(IModHelper helper, bool automatic = false)
    {
        // Manual exports are snapshots. The automatically prepared catalogue
        // uses one managed cache, published only when the whole export succeeds.
        string output = automatic
            ? Path.Combine(helper.DirectoryPath, "cache", "library.pending")
            : Path.Combine(helper.DirectoryPath, "exports", DateTime.UtcNow.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture) + "-" + Guid.NewGuid().ToString("N"));
        string previous = Path.Combine(helper.DirectoryPath, "cache", "library.previous");
        if (automatic)
        {
            // Fixed staging/previous names bound disk use even after a crash or
            // a locked-file cleanup failure. Existing files are never removed
            // to make room; the author can recover them before the next refresh.
            if (Directory.Exists(output) || File.Exists(output) || Directory.Exists(previous) || File.Exists(previous))
                throw new InvalidDataException("A previous library preparation is still in cache/library.pending or cache/library.previous. Its files were preserved; recover them before refreshing.");
            Directory.CreateDirectory(output);
        }
        try
        {
            Result result = ExportInto(helper, output);
            if (!automatic) return result;
            string cache = Path.Combine(helper.DirectoryPath, "cache", "library");
            bool movedPrevious = false;
            try
            {
                if (Directory.Exists(cache))
                {
                    // Refuse unexpected contents or linked caches. Never delete
                    // a folder simply because a user gave it the expected name.
                    if (!IsUnchangedManagedCache(cache))
                        throw new InvalidDataException("The library cache contains changed or unrecognized files. They were kept intact; move them out of cache/library before refreshing the game library.");
                    Directory.Move(cache, previous);
                    movedPrevious = true;
                }
                Directory.Move(output, cache);
            }
            catch
            {
                if (movedPrevious && !Directory.Exists(cache)) Directory.Move(previous, cache);
                throw;
            }
            if (movedPrevious)
            {
                try
                {
                    // Verify once more after moving it. User-added or edited
                    // artwork is preserved even inside this generated folder.
                    if (IsUnchangedManagedCache(previous)) Directory.Delete(previous, recursive: true);
                }
                catch { /* A locked old cache must not invalidate the ready new library. */ }
            }
            return result with { Path = cache };
        }
        catch
        {
            // Only staging created by this invocation is removed. The existence
            // check above is outside this try block, so old staging is preserved.
            if (automatic && Directory.Exists(output))
            {
                try { Directory.Delete(output, recursive: true); }
                catch { }
            }
            throw;
        }
    }

    private static Result ExportInto(IModHelper helper, string output)
    {
        Directory.CreateDirectory(Path.Combine(output, "textures"));
        var definitions = new List<Dictionary<string, object?>>();
        var notes = new List<string>
        {
            "These are private previews from the installed game's current Data/Furniture and resolved textures. They do not supply furniture mods.",
            "The library captures each item's default appearance and rotations. Custom drawing, lighting effects, animation, and Alternative Textures variants still run in-game; they are not enumerated as preview animations.",
            "The game's item registry does not identify the mod that owns each item. Set required mod dependencies for custom furniture in Pixelheart before sharing its design."
        };
        var messages = new List<string>();
        var sheets = new Dictionary<Texture2D, Sheet>();
        var failedSheets = new HashSet<Texture2D>();
        long textureBytes = 0;
        long jsonBytes = 2048;
        Dictionary<string, string> records = helper.GameContent.Load<Dictionary<string, string>>("Data/Furniture");
        if (records.Count > MaxItems) messages.Add($"The catalogue exceeds {MaxItems} records; only the first {MaxItems} sorted IDs were considered.");
        foreach ((string id, string encoded) in records.OrderBy(pair => pair.Key, StringComparer.Ordinal).Take(MaxItems))
        {
            try
            {
                string qualified = id.StartsWith("(F)", StringComparison.Ordinal) ? id : "(F)" + id;
                if (qualified.Length > 259 || qualified.Any(char.IsControl)) throw new InvalidDataException("Unsupported item identifier.");
                string[] fields = encoded.Split('/');
                if (fields.Length < 7 || !int.TryParse(fields[4], out int rotations) || (rotations != 1 && rotations != 2 && rotations != 4))
                    throw new InvalidDataException("Unsupported native furniture record or rotation count.");
                if (!int.TryParse(fields[6], out int restriction) || restriction < -1 || restriction > 2)
                    throw new InvalidDataException("Unsupported native placement restriction.");
                string textureName = fields.Length > 9 && fields[9].Length > 0 ? fields[9].Replace('\\', '/') : "TileSheets/furniture";
                if (!SafeAssetName(textureName)) throw new InvalidDataException("Unsupported texture asset name.");
                Furniture furniture = ItemRegistry.Create<Furniture>(qualified);
                if (furniture.QualifiedItemId != qualified) throw new InvalidDataException("The registry did not create the requested furniture.");
                var metadata = ItemRegistry.GetData(qualified) ?? throw new InvalidDataException("The registry did not expose parsed item data.");
                Texture2D texture = metadata.GetTexture();
                if (failedSheets.Contains(texture)) throw new InvalidDataException("This item's texture could not be exported within the library limits.");
                furniture.SetPlacement(0, 0, 0);
                var observed = new List<(Rectangle Source, bool Flipped, int[] Footprint)>();
                for (int rotation = 0; rotation < rotations; rotation++)
                {
                    // Only read the installed game's public state. No private-field probes,
                    // hardcoded type-size tables, or reconstructed game rotation algorithm.
                    if (!TryReadPublic(furniture, "sourceRect", out Rectangle rect)
                        || !TryReadPublic(furniture, "flipped", out bool flipped))
                        throw new InvalidDataException("The installed furniture type does not expose supported public sprite/flip state.");
                    if (rect.X < 0 || rect.Y < 0 || rect.Width < 1 || rect.Height < 1 || rect.Width > 2048 || rect.Height > 2048
                        || rect.Right > texture.Width || rect.Bottom > texture.Height || rect.Width % 16 != 0 || rect.Height % 16 != 0)
                        throw new InvalidDataException("The resolved sprite rectangle is outside the texture or unsupported by the editor.");
                    Rectangle bounds = furniture.GetBoundingBox();
                    if (bounds.Width < 64 || bounds.Height < 64 || bounds.Width > 128 * 64 || bounds.Height > 128 * 64
                        || bounds.Width % 64 != 0 || bounds.Height % 64 != 0)
                        throw new InvalidDataException("The resolved collision footprint is not a supported whole-tile rectangle.");
                    observed.Add((rect, flipped, new[] { bounds.Width / 64, bounds.Height / 64 }));
                    if (rotation + 1 < rotations) furniture.rotate();
                }
                if (!sheets.TryGetValue(texture, out Sheet? sheet))
                {
                    try
                    {
                        sheet = SaveSheet(texture, output, sheets.Count, textureBytes);
                        textureBytes += new FileInfo(Path.Combine(output, sheet.Path)).Length;
                        sheets.Add(texture, sheet);
                    }
                    catch { failedSheets.Add(texture); throw; }
                }
                var frames = new List<Dictionary<string, object>>();
                var footprints = new Dictionary<string, int[]>();
                for (int rotation = 0; rotation < observed.Count; rotation++)
                {
                    var state = observed[rotation];
                    // The right half of the preview atlas is a mirror of the original.
                    // Preview rectangles therefore remain correct without changing the
                    // game's asset or relying on editor-specific flip behavior.
                    int x = state.Flipped ? sheet.OriginalWidth * 2 - state.Source.Right : state.Source.X;
                    frames.Add(new Dictionary<string, object>
                    {
                        ["rotation"] = rotation,
                        ["rect"] = new[] { x, state.Source.Y, state.Source.Width, state.Source.Height },
                        ["duration_ms"] = 100
                    });
                    footprints[rotation.ToString(CultureInfo.InvariantCulture)] = state.Footprint;
                }
                var definition = new Dictionary<string, object?>
                {
                    ["id"] = qualified,
                    ["name"] = CleanLabel(furniture.DisplayName, 256, id),
                    ["kind"] = CleanLabel(fields[1], 64, "other"),
                    ["footprint"] = observed[0].Footprint,
                    ["rotation_footprints"] = footprints,
                    ["sprite_size"] = new[] { observed[0].Source.Width / 16, observed[0].Source.Height / 16 },
                    ["rotations"] = rotations,
                    ["sprite_index"] = null,
                    ["texture"] = textureName,
                    ["preview_asset"] = sheet.Path,
                    ["frames"] = frames,
                    ["placement"] = restriction switch { 0 => "indoors", 1 => "outdoors", 2 => "both", _ => "default" },
                    ["dependency"] = "",
                    ["mod_data"] = new Dictionary<string, string>()
                };
                long definitionBytes = Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(definition));
                if (jsonBytes + definitionBytes > MaxJsonBytes - 1024 * 1024)
                { messages.Add("The library reached its JSON size limit; remaining furniture was omitted."); break; }
                jsonBytes += definitionBytes;
                definitions.Add(definition);
            }
            catch (Exception ex)
            {
                // One custom renderer or oversized texture must not lose the rest of a catalogue.
                if (messages.Count < 1000) messages.Add($"{CleanLabel(id, 256, "unknown")}: skipped ({CleanLabel(ex.Message, 400, "unsupported item")}).");
            }
        }
        if (definitions.Count == 0)
        {
            string notes = Path.Combine(output, "warnings.txt");
            using var diagnostic = new FileStream(notes, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            byte[] details = Encoding.UTF8.GetBytes(string.Join(Environment.NewLine, messages));
            diagnostic.Write(details, 0, details.Length);
            throw new InvalidOperationException($"No compatible furniture previews could be resolved. Check this runtime against the installed game version; diagnostic notes: {notes}");
        }
        var surfaces = ExportSurfaces(helper, output, sheets, failedSheets, messages, ref textureBytes, ref jsonBytes);
        var library = new { format = "pixelheart-interior-library", version = 1, definitions, surfaces, warnings = messages, notes };
        byte[] payload = Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(library));
        if (payload.Length > MaxJsonBytes) throw new InvalidDataException("The generated library exceeds the editor's JSON size limit.");
        using (var stream = new FileStream(Path.Combine(output, "library.json"), FileMode.CreateNew, FileAccess.Write, FileShare.None))
            stream.Write(payload, 0, payload.Length);
        WriteCacheOwnership(output);
        return new Result(output, definitions.Count, messages.Count);
    }

    private static void WriteCacheOwnership(string root)
    {
        var ownership = new CacheOwnership();
        ownership.Files["library.json"] = HashFile(Path.Combine(root, "library.json"));
        foreach (string texture in Directory.EnumerateFiles(Path.Combine(root, "textures")).Take(MaxSheets))
            ownership.Files["textures/" + Path.GetFileName(texture)] = HashFile(texture);
        File.WriteAllText(Path.Combine(root, ".pixelheart-library-cache"), JsonConvert.SerializeObject(ownership));
    }

    private static string HashFile(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        using var hash = SHA256.Create();
        return Convert.ToHexString(hash.ComputeHash(stream));
    }

    private static bool IsUnchangedManagedCache(string root)
    {
        try
        {
            if ((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0) return false;
            string marker = Path.Combine(root, ".pixelheart-library-cache");
            if (!File.Exists(marker) || (File.GetAttributes(marker) & FileAttributes.ReparsePoint) != 0
                || new FileInfo(marker).Length > 256 * 1024) return false;
            CacheOwnership? ownership = JsonConvert.DeserializeObject<CacheOwnership>(File.ReadAllText(marker));
            if (ownership == null || ownership.Version != 1 || ownership.Files == null
                || ownership.Files.Count < 1 || ownership.Files.Count > MaxSheets + 1
                || !ownership.Files.ContainsKey("library.json")) return false;
            var actual = new HashSet<string>(StringComparer.Ordinal);
            foreach (string entry in Directory.EnumerateFileSystemEntries(root).Take(4))
            {
                string name = Path.GetFileName(entry);
                FileAttributes attributes = File.GetAttributes(entry);
                if ((attributes & FileAttributes.ReparsePoint) != 0) return false;
                if (name == "textures" && (attributes & FileAttributes.Directory) != 0)
                {
                    foreach (string texture in Directory.EnumerateFileSystemEntries(entry).Take(MaxSheets + 1))
                    {
                        FileAttributes textureAttributes = File.GetAttributes(texture);
                        if ((textureAttributes & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != 0) return false;
                        actual.Add("textures/" + Path.GetFileName(texture));
                    }
                }
                else if (name == "library.json" && (attributes & FileAttributes.Directory) == 0) actual.Add(name);
                else if (name != ".pixelheart-library-cache") return false;
            }
            if (!actual.SetEquals(ownership.Files.Keys)) return false;
            long bytes = 0;
            foreach ((string relative, string expectedHash) in ownership.Files)
            {
                // The actual-set equality above only allows immediate generated
                // files. No marker entry can reach a parent or nested folder.
                string path = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
                long length = new FileInfo(path).Length;
                if (length > (relative == "library.json" ? MaxJsonBytes : MaxPngBytes)) return false;
                bytes += length;
                if (bytes > MaxTotalPngBytes + MaxJsonBytes || HashFile(path) != expectedHash) return false;
            }
            return true;
        }
        catch { return false; }
    }

    private static List<Dictionary<string, object?>> ExportSurfaces(IModHelper helper, string output,
        Dictionary<Texture2D, Sheet> sheets, HashSet<Texture2D> failedSheets, List<string> messages,
        ref long textureBytes, ref long jsonBytes)
    {
        var result = new List<Dictionary<string, object?>>();
        foreach ((string prefix, string kind, int width, int height) in new[] { ("(WP)", "wall", 16, 48), ("(FL)", "floor", 32, 32) })
        {
            try
            {
                // Observe the installed registry's public enumeration and sprite
                // data. No vanilla ID range, sheet offsets, or mod algorithms
                // are reconstructed. Unsupported API versions fail visibly.
                object metadata = ItemRegistry.GetMetadata(prefix + "0");
                object? definition = metadata.GetType().GetMethod("GetTypeDefinition", BindingFlags.Public | BindingFlags.Instance,
                    null, Type.EmptyTypes, null)?.Invoke(metadata, null);
                object? rawIds = definition?.GetType().GetMethod("GetAllIds", BindingFlags.Public | BindingFlags.Instance,
                    null, Type.EmptyTypes, null)?.Invoke(definition, null);
                if (rawIds is not IEnumerable<string> ids)
                    throw new InvalidDataException("The installed game does not expose supported pattern enumeration.");
                foreach (string localId in ids.Take(MaxItems))
                {
                    if (result.Count >= MaxSurfaces)
                    {
                        messages.Add($"The pattern library reached its {MaxSurfaces}-pattern limit; remaining patterns were omitted.");
                        return result;
                    }
                    try
                    {
                        string qualified = localId.StartsWith(prefix, StringComparison.Ordinal) ? localId : prefix + localId;
                        if (qualified.Length > 260 || qualified.Any(char.IsControl)) throw new InvalidDataException("Unsupported pattern identifier.");
                        var data = ItemRegistry.GetData(qualified) ?? throw new InvalidDataException("Pattern metadata is unavailable.");
                        Texture2D texture = data.GetTexture();
                        Rectangle rectangle = data.GetSourceRect();
                        if (rectangle.Width != width || rectangle.Height != height || rectangle.X < 0 || rectangle.Y < 0
                            || rectangle.Right > texture.Width || rectangle.Bottom > texture.Height)
                            throw new InvalidDataException("The registry did not expose a complete wallpaper or flooring pattern.");
                        if (!TryReadPublic(data, "TextureName", out string textureName) || !SafeAssetName(textureName))
                            throw new InvalidDataException("The pattern's game texture name is unavailable.");
                        if (failedSheets.Contains(texture)) throw new InvalidDataException("The pattern texture exceeds preview limits.");
                        if (!sheets.TryGetValue(texture, out Sheet? sheet))
                        {
                            try
                            {
                                sheet = SaveSheet(texture, output, sheets.Count, textureBytes);
                                textureBytes += new FileInfo(Path.Combine(output, sheet.Path)).Length;
                                sheets.Add(texture, sheet);
                            }
                            catch { failedSheets.Add(texture); throw; }
                        }
                        string displayName = TryReadPublic(data, "DisplayName", out string label) ? label : kind;
                        var surface = new Dictionary<string, object?>
                        {
                            ["id"] = qualified, ["kind"] = kind,
                            ["name"] = CleanLabel(displayName + " · " + localId, 256, qualified),
                            ["texture"] = textureName, ["preview_asset"] = sheet.Path,
                            ["rect"] = new[] { rectangle.X, rectangle.Y, width, height },
                            ["dependency"] = ""
                        };
                        long bytes = Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(surface));
                        if (jsonBytes + bytes > MaxJsonBytes - 512 * 1024) break;
                        result.Add(surface);
                        jsonBytes += bytes;
                    }
                    catch (Exception ex)
                    {
                        if (messages.Count < 1000) messages.Add($"{prefix}{CleanLabel(localId, 256, "unknown")}: pattern skipped ({CleanLabel(ex.Message, 400, "unsupported pattern")}).");
                    }
                }
            }
            catch (Exception ex)
            {
                if (messages.Count < 1000) messages.Add($"{kind} patterns unavailable: {CleanLabel(ex.Message, 400, "unsupported pattern API")}");
            }
        }
        return result;
    }

    private static Sheet SaveSheet(Texture2D source, string output, int index, long bytesWritten)
    {
        if (index >= MaxSheets) throw new InvalidDataException("The library reached its texture count limit.");
        int width = source.Width, height = source.Height;
        if (width < 1 || height < 1 || width > 32768 || height > 65535 || (long)width * height * 2 > 16_777_216)
            throw new InvalidDataException("The resolved texture exceeds the editor's pixel limit.");
        Color[] original = new Color[width * height];
        source.GetData(original);
        Color[] pixels = new Color[width * 2 * height];
        for (int y = 0; y < height; y++)
            for (int x = 0; x < width; x++)
            {
                pixels[y * width * 2 + x] = original[y * width + x];
                pixels[y * width * 2 + width * 2 - x - 1] = original[y * width + x];
            }
        using var preview = new Texture2D(source.GraphicsDevice, width * 2, height);
        preview.SetData(pixels);
        using var encoded = new MemoryStream();
        preview.SaveAsPng(encoded, preview.Width, preview.Height);
        if (encoded.Length > MaxPngBytes || bytesWritten + encoded.Length > MaxTotalPngBytes)
            throw new InvalidDataException("The resolved texture exceeds the editor's PNG byte limit.");
        string relative = $"textures/sheet-{index:D4}.png";
        using var target = new FileStream(Path.Combine(output, relative), FileMode.CreateNew, FileAccess.Write, FileShare.None);
        encoded.Position = 0;
        encoded.CopyTo(target);
        return new Sheet(relative, width, height);
    }

    private static bool TryReadPublic<T>(object instance, string name, out T value)
    {
        const BindingFlags flags = BindingFlags.Public | BindingFlags.Instance;
        Type type = instance.GetType();
        object? raw = type.GetProperty(name, flags)?.GetValue(instance) ?? type.GetField(name, flags)?.GetValue(instance);
        if (raw is T direct) { value = direct; return true; }
        // Netcode wrappers expose their live value through a public Value property.
        if (raw != null && raw.GetType().GetProperty("Value", flags)?.GetValue(raw) is T wrapped)
        { value = wrapped; return true; }
        value = default!;
        return false;
    }

    private static string CleanLabel(string value, int maximum, string fallback)
    {
        string clean = new(value.Where(character => !char.IsControl(character)).Take(maximum).ToArray());
        return clean.Length > 0 ? clean : fallback;
    }

    private static bool SafeAssetName(string value) => value.Length is > 0 and <= 1024
        && !value.Contains(':') && !value.Any(char.IsControl)
        && value.Split('/').All(part => part.Length > 0 && part != "." && part != "..");
}
