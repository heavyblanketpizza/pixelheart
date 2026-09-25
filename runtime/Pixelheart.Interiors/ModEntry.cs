using System.Reflection;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Locations;
using StardewValley.Objects;

namespace Pixelheart.Interiors;

/// <summary>Consumes Pixelheart's own versioned data contract; no other editor's format or code.</summary>
public sealed class ModEntry : Mod
{
    internal const string AssetName = "Pixelheart.Interiors/Designs";
    internal const string SpouseMarker = "Pixelheart.Interiors/SpouseRoom";
    private readonly HashSet<string> warnings = new(StringComparer.Ordinal);
    private Dictionary<string, DesignData>? designs;
    private bool libraryQueued;
    private bool libraryAttempted;
    private FurnitureActivities? furnitureActivities;
    private FurnitureEffects? furnitureEffects;

    public override void Entry(IModHelper helper)
    {
        furnitureActivities = new FurnitureActivities(helper, Monitor);
        furnitureEffects = new FurnitureEffects(helper, Monitor);
        helper.Events.Content.AssetRequested += OnAssetRequested;
        helper.Events.Content.AssetsInvalidated += OnAssetsInvalidated;
        helper.Events.GameLoop.SaveLoaded += (_, _) => { InitializeWorld(); libraryQueued = !libraryAttempted; };
        helper.Events.GameLoop.UpdateTicked += (_, _) => PrepareLibrary();
        helper.Events.GameLoop.DayStarted += (_, _) => InitializeWorld();
        helper.Events.GameLoop.ReturnedToTitle += (_, _) => { designs = null; warnings.Clear(); };
        helper.Events.Player.Warped += (_, e) => { if (e.IsLocalPlayer) InitializeWorld(); };
        helper.Events.Input.ButtonPressed += OnButtonPressed;
        helper.ConsoleCommands.Add("pixelheart_rooms", "List authored rooms in the current residence.", ListRooms);
        helper.ConsoleCommands.Add("pixelheart_room", "Change an empty room: pixelheart_room <location> <roomId> <on|off>", RoomCommand);
        helper.ConsoleCommands.Add("pixelheart_interiors_retry", "Retry blocked initial furniture without replacing anything.", (_, _) => InitializeWorld());
        helper.ConsoleCommands.Add("pixelheart_export_furniture", "Export this installation's resolved furniture previews for Pixelheart into a new local exports folder.", ExportFurniture);
    }

    private void OnAssetRequested(object? sender, AssetRequestedEventArgs e)
    {
        if (e.NameWithoutLocale.IsEquivalentTo(AssetName))
            e.LoadFrom(() => new Dictionary<string, DesignData>(), AssetLoadPriority.Low);
    }

    private void PrepareLibrary()
    {
        // Wait until every SaveLoaded subscriber has run so installed mods can
        // finish their save-dependent content updates. Work runs at most once
        // per launch, never once per warp, day, or update tick.
        if (!libraryQueued || libraryAttempted || !Context.IsWorldReady) return;
        libraryQueued = false;
        libraryAttempted = true;
        try
        {
            FurnitureLibraryExporter.Result result = FurnitureLibraryExporter.Export(Helper, automatic: true);
            Monitor.Log($"Your interior design library is ready for Pixelheart ({result.Count} furniture items). Choose this game's folder in the designer to connect it.", LogLevel.Info);
        }
        catch (Exception ex)
        {
            Monitor.Log($"Pixelheart could not prepare the interior design library: {ex.Message}. Existing library files were kept. Restart the game to try again, or use pixelheart_export_furniture for diagnostic details.", LogLevel.Warn);
        }
    }

    private void OnAssetsInvalidated(object? sender, AssetsInvalidatedEventArgs e)
    {
        if (e.NamesWithoutLocale.Any(name => name.IsEquivalentTo(AssetName)))
            designs = null;
    }

    private Dictionary<string, DesignData> GetDesigns() => designs ??= Helper.GameContent.Load<Dictionary<string, DesignData>>(AssetName);

    private static IEnumerable<GameLocation> AllLocations()
    {
        var pending = new Stack<GameLocation>(Game1.locations);
        var visited = new HashSet<GameLocation>();
        while (pending.Count > 0)
        {
            GameLocation location = pending.Pop();
            if (!visited.Add(location)) continue;
            yield return location;
            foreach (GameLocation interior in location.GetInstancedBuildingInteriors()) pending.Push(interior);
        }
    }

    private void InitializeWorld()
    {
        if (!Context.IsWorldReady || !Context.IsMainPlayer) return;
        try
        {
            foreach ((string id, DesignData design) in GetDesigns())
            {
                if (!Validate(id, design, out string error)) { Warn(id, error); continue; }
                foreach ((GameLocation location, Point origin) in ResolveTargets(id, design))
                {
                    try
                    {
                        if (string.IsNullOrEmpty(design.SpouseNpc)) ApplySavedVariant(id, design, location);
                        InitializeFurniture(id, design, location, origin);
                    }
                    catch (Exception ex) { Warn($"{id}/{location.Name}", $"Interior {id} was left unchanged or partially initialized safely: {ex.Message}"); }
                }
            }
        }
        catch (Exception ex) { Warn("load", $"Could not load interior definitions: {ex.Message}"); }
    }

    private IEnumerable<(GameLocation Location, Point Origin)> ResolveTargets(string id, DesignData design)
    {
        foreach (GameLocation location in AllLocations())
        {
            if (string.IsNullOrEmpty(design.SpouseNpc))
            {
                if (location.Name == design.Location) yield return (location, Point.Zero);
                continue;
            }
            if (location is not FarmHouse house || !house.HasNpcSpouseOrRoommate(design.SpouseNpc)) continue;
            var layer = location.Map?.GetLayer("Back");
            if (layer == null) continue;
            var markers = new List<Point>();
            for (int y = 0; y < layer.LayerHeight; y++)
                for (int x = 0; x < layer.LayerWidth; x++)
                {
                    var tile = layer.Tiles[x, y];
                    if (tile != null && tile.Properties.TryGetValue(SpouseMarker, out var value) && value.ToString() == id)
                        markers.Add(new Point(x - design.SpouseMarkerX, y - design.SpouseMarkerY));
                    else if (tile != null && tile.TileIndexProperties.TryGetValue(SpouseMarker, out var indexValue) && indexValue.ToString() == id)
                        markers.Add(new Point(x - design.SpouseMarkerX, y - design.SpouseMarkerY));
                }
            if (markers.Count == 1 && markers[0].X >= 0 && markers[0].Y >= 0
                && markers[0].X + design.Width <= layer.LayerWidth && markers[0].Y + design.Height <= layer.LayerHeight)
                yield return (location, markers[0]);
            else Warn($"marker/{id}/{location.Name}", $"Cannot locate exactly one spouse-room marker for {id} in {location.Name}; furniture initialization is deferred.");
        }
    }

    private void InitializeFurniture(string id, DesignData design, GameLocation location, Point origin)
    {
        var pending = new List<(PlacementData Placement, Furniture Item)>();
        HashSet<string> enabled = EnabledRooms(id, design, location);
        foreach (PlacementData placement in design.Furniture)
        {
            if (location.modData.ContainsKey(PlacementKey(id, placement.Id))) continue;
            // Recover a native placement which completed before its callback threw.
            if (location.furniture.Any(item => item.modData.TryGetValue("Pixelheart.Interiors/Placement", out string value) && value == id + "/" + placement.Id))
            {
                location.modData[PlacementKey(id, placement.Id)] = "1";
                continue;
            }
            if (design.Rooms.Any(room => room.Optional && !enabled.Contains(room.Id) && RoomPlacementRect(room).Contains(placement.X, placement.Y))) continue;
            if (!ItemRegistry.Exists(placement.ItemId))
                throw new InvalidOperationException($"Furniture '{placement.ItemId}' is not supplied by the installed mods.");
            Furniture item = ItemRegistry.Create<Furniture>(placement.ItemId);
            if (item.QualifiedItemId != placement.ItemId)
                throw new InvalidOperationException($"Furniture '{placement.ItemId}' is not supplied by the installed mods.");
            item.SetPlacement(origin.X + placement.X, origin.Y + placement.Y, 0);
            for (int turn = 0; turn < placement.Rotation; turn++) item.rotate();
            foreach ((string key, string value) in placement.ModData) item.modData[key] = value;
            if (placement.HeldItem != null) PrepareHeldItem(item, placement.HeldItem, location);
            pending.Add((placement, item));
        }
        // Resolve every pending item before mutation. A missing furniture mod never substitutes an error item.
        foreach ((PlacementData placement, Furniture item) in pending)
        {
            Point tile = new(origin.X + placement.X, origin.Y + placement.Y);
            if (!CanPlace(item, location, tile, out string reason))
            {
                Warn($"placement/{id}/{placement.Id}/{location.Name}", $"Deferred {placement.ItemId} in {location.Name}: {reason} Move the obstruction and use pixelheart_interiors_retry.");
                continue;
            }
            item.modData["Pixelheart.Interiors/Placement"] = id + "/" + placement.Id;
            if (!PlaceNative(item, location, tile, out reason))
            {
                Warn($"native-placement/{id}/{placement.Id}/{location.Name}", $"Deferred {placement.ItemId} in {location.Name}: {reason}");
                continue;
            }
            // This remains after the player picks up or moves the item, so later loads cannot duplicate it.
            location.modData[PlacementKey(id, placement.Id)] = "1";
        }
    }

    private static void PrepareHeldItem(Furniture table, HeldItemData data, GameLocation location)
    {
        // Prepare the complete table before world mutation. Its normal placement marker
        // covers both items: taking the decoration later must never refill the table.
        if (table.furniture_type.Value is not (Furniture.table or Furniture.longTable) || table.heldObject.Value != null)
            throw new InvalidOperationException("An initial held decoration requires an empty table.");
        if (!ItemRegistry.Exists(data.ItemId))
            throw new InvalidOperationException($"Tabletop decoration '{data.ItemId}' is not supplied by the installed mods.");
        Furniture decoration = ItemRegistry.Create<Furniture>(data.ItemId);
        if (decoration.QualifiedItemId != data.ItemId || decoration.furniture_type.Value != Furniture.decor
            || decoration.getTilesWide() != 1 || decoration.getTilesHigh() != 1 || decoration.heldObject.Value != null)
            throw new InvalidOperationException($"Tabletop item '{data.ItemId}' must be a one-tile decoration.");
        foreach ((string key, string value) in data.ModData) decoration.modData[key] = value;
        table.Location = location;
        // This callback copies the item and applies native tabletop placement behavior.
        // A null farmer is intentional: supplying a farmer consumes their active item.
        if (!table.performObjectDropInAction(decoration, true, null, false)
            || !table.performObjectDropInAction(decoration, false, null, false))
            throw new InvalidOperationException($"The game rejected tabletop decoration '{data.ItemId}'.");
    }

    private static bool PlaceNative(Furniture item, GameLocation location, Point tile, out string reason)
    {
        // Invoke the public native placement callback so item subclasses and installed mods
        // retain their placement behavior (including lights). Never insert items directly.
        MethodInfo? method = item.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance)
            .Where(candidate => candidate.Name == "placementAction" && candidate.ReturnType == typeof(bool))
            .FirstOrDefault(candidate =>
            {
                ParameterInfo[] parameters = candidate.GetParameters();
                return parameters.Length >= 3 && parameters[0].ParameterType == typeof(GameLocation)
                    && parameters[1].ParameterType == typeof(int) && parameters[2].ParameterType == typeof(int)
                    && parameters.Skip(3).All(p => p.ParameterType == typeof(Farmer) || p.HasDefaultValue);
            });
        if (method == null) { reason = "The game's furniture placement callback is unsupported by this runtime."; return false; }
        object?[] args = method.GetParameters().Select(p => p.ParameterType == typeof(Farmer) ? (object)Game1.player : p.HasDefaultValue ? p.DefaultValue : null).ToArray();
        args[0] = location;
        args[1] = tile.X * 64;
        args[2] = tile.Y * 64;
        if (method.Invoke(item, args) is not true) { reason = "The native furniture placement callback rejected this item."; return false; }
        reason = "";
        return true;
    }

    private void ExportFurniture(string command, string[] args)
    {
        if (!Context.IsWorldReady) { Monitor.Log("Load a save with the furniture mods you want to use first.", LogLevel.Info); return; }
        if (args.Length != 0) { Monitor.Log("Use pixelheart_export_furniture without arguments.", LogLevel.Info); return; }
        try
        {
            FurnitureLibraryExporter.Result result = FurnitureLibraryExporter.Export(Helper);
            Monitor.Log($"Exported {result.Count} furniture definitions to {result.Path}. Import library.json in Pixelheart. {result.WarningCount} notes are saved in the library.", LogLevel.Info);
        }
        catch (Exception ex) { Monitor.Log($"Could not export the furniture library: {ex.Message}", LogLevel.Error); }
    }

    private static bool CanPlace(Furniture item, GameLocation location, Point tile, out string reason)
    {
        var back = location.Map?.GetLayer("Back");
        if (back == null || tile.X < 0 || tile.Y < 0 || tile.X >= back.LayerWidth || tile.Y >= back.LayerHeight)
        { reason = "The placement is outside the live map."; return false; }
        // Ask the installed game's public placement rule; optional arguments vary between game releases.
        // No fallback bypasses validation when that API is unavailable.
        MethodInfo? method = item.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance)
            .Where(candidate => candidate.Name == "canBePlacedHere" && candidate.ReturnType == typeof(bool))
            .FirstOrDefault(candidate =>
            {
                ParameterInfo[] parameters = candidate.GetParameters();
                return parameters.Length >= 2 && parameters[0].ParameterType == typeof(GameLocation)
                    && parameters[1].ParameterType == typeof(Vector2) && parameters.Skip(2).All(p => p.HasDefaultValue);
            });
        if (method == null) { reason = "The game's furniture placement API is unsupported by this runtime."; return false; }
        object?[] args = method.GetParameters().Select(p => p.HasDefaultValue ? p.DefaultValue : null).ToArray();
        args[0] = location;
        args[1] = new Vector2(tile.X, tile.Y);
        if (method.Invoke(item, args) is not true) { reason = "The game rejected this occupied or unsuitable tile."; return false; }
        Rectangle bounds = item.GetBoundingBox();
        if (location.characters.Any(character => character.GetBoundingBox().Intersects(bounds))
            || location.farmers.Any(farmer => farmer.GetBoundingBox().Intersects(bounds)))
        { reason = "A character is standing in the placement area."; return false; }
        reason = "";
        return true;
    }

    private static string PlacementKey(string design, string placement) => $"Pixelheart.Interiors/Placed/{design}/{placement}";
    private static string VariantKey(string design) => $"Pixelheart.Interiors/Variant/{design}";
    private static Rectangle RoomPlacementRect(RoomData room) => new(room.X, room.Y - 3, room.Width, room.Height + 3);

    private static VariantData? CurrentVariant(string id, DesignData design, GameLocation location)
    {
        string current = location.modData.TryGetValue(VariantKey(id), out string saved) ? saved : design.DefaultVariant;
        return design.Variants.FirstOrDefault(variant => variant.Id == current);
    }

    private static HashSet<string> EnabledRooms(string id, DesignData design, GameLocation location) =>
        new(CurrentVariant(id, design, location)?.EnabledRooms ?? design.Rooms.Where(r => r.Enabled).Select(r => r.Id).ToList(), StringComparer.Ordinal);

    private void ApplySavedVariant(string id, DesignData design, GameLocation location)
    {
        VariantData? variant = CurrentVariant(id, design, location);
        if (variant == null) return;
        if (location.mapPath.Value == variant.MapAsset) return;
        // Load before changing the saved path, so a missing map cannot strand a resident.
        var nextMap = Helper.GameContent.Load<xTile.Map>(variant.MapAsset);
        var floorLayer = nextMap.GetLayer("Back");
        if (floorLayer == null || floorLayer.LayerWidth != design.Width || floorLayer.LayerHeight != design.Height)
            throw new InvalidOperationException("The room map does not match the authored fixed interior bounds.");
        string previous = location.mapPath.Value;
        try { location.mapPath.Value = variant.MapAsset; location.reloadMap(); }
        catch { location.mapPath.Value = previous; location.reloadMap(); throw; }
    }

    private string ToggleRoom(string id, DesignData design, GameLocation location, string roomId, bool enabled)
    {
        if (!Context.IsMainPlayer) return "Only the host can change residence rooms.";
        if (Context.IsMultiplayer) return "Room changes are currently available in single-player; furniture remains usable in multiplayer.";
        if (!string.IsNullOrEmpty(design.SpouseNpc)) return "Spouse rooms use the farmhouse room structure.";
        RoomData? room = design.Rooms.FirstOrDefault(candidate => candidate.Id == roomId && candidate.Optional && !RoomConnections.IsStairway(candidate));
        if (room == null) return "This room is not an optional room in the residence.";
        HashSet<string> active = EnabledRooms(id, design, location);
        if (active.Contains(roomId) == enabled) return "That room already has the requested state.";
        RoomData[] group = RoomConnections.ToggleGroup(design, room);
        if (!enabled && group.Any(member => IsOccupied(design, location, member)))
            return "Empty this room and its stairs, and move any resident or standing point clear before removing it.";
        foreach (RoomData member in group)
            if (enabled) active.Add(member.Id); else active.Remove(member.Id);
        VariantData? variant = design.Variants.FirstOrDefault(candidate => active.SetEquals(candidate.EnabledRooms));
        if (variant == null) return "No exported map exists for this room combination.";
        string key = VariantKey(id);
        bool hadPrevious = location.modData.TryGetValue(key, out string previous);
        try
        {
            Helper.GameContent.Load<xTile.Map>(variant.MapAsset);
            location.modData[key] = variant.Id;
            ApplySavedVariant(id, design, location);
        }
        catch (Exception ex)
        {
            if (hadPrevious) location.modData[key] = previous; else location.modData.Remove(key);
            ApplySavedVariant(id, design, location);
            return $"Could not change the room: {ex.Message}";
        }
        // Once a room opens, never close it again because a furniture dependency is missing.
        // Earlier placements may already exist there, and are saved independently.
        try { InitializeFurniture(id, design, location, Point.Zero); }
        catch (Exception ex) { Warn($"room-furniture/{id}/{roomId}", $"Room changed, but its initial furniture is deferred: {ex.Message}"); }
        return $"{room.Name}: {(enabled ? "added" : "removed")}.";
    }

    private static bool IsOccupied(DesignData design, GameLocation location, RoomData room)
    {
        // Include the upper wall and the surrounding structural tiles, not only walkable floor.
        Rectangle tiles = Rectangle.Intersect(new Rectangle(room.X - 1, room.Y - 3, room.Width + 2, room.Height + 4), new Rectangle(0, 0, design.Width, design.Height));
        Rectangle pixels = new(tiles.X * 64, tiles.Y * 64, tiles.Width * 64, tiles.Height * 64);
        if (design.Entry.Length == 2 && tiles.Contains(design.Entry[0], design.Entry[1])) return true;
        if (!string.IsNullOrEmpty(design.SpouseNpc) && design.SpouseStand.Length == 2
            && tiles.Contains(design.SpouseStand[0], design.SpouseStand[1])) return true;
        if (design.ProtectedTiles.Any(tile => tiles.Contains(tile[0], tile[1]))) return true;
        return location.furniture.Any(item => item.GetBoundingBox().Intersects(pixels))
            || location.Objects.Pairs.Any(pair => tiles.Contains((int)pair.Key.X, (int)pair.Key.Y))
            || location.terrainFeatures.Pairs.Any(pair => tiles.Contains((int)pair.Key.X, (int)pair.Key.Y))
            || location.characters.Any(character => character.GetBoundingBox().Intersects(pixels))
            || location.farmers.Any(farmer => farmer.GetBoundingBox().Intersects(pixels));
    }

    private void OnButtonPressed(object? sender, ButtonPressedEventArgs e)
    {
        if (e.Button != SButton.F8 || !Context.IsPlayerFree) return;
        var match = GetDesigns().FirstOrDefault(pair => string.IsNullOrEmpty(pair.Value.SpouseNpc) && pair.Value.Location == Game1.currentLocation.Name);
        if (match.Value == null || !Validate(match.Key, match.Value, out _)) return;
        RoomData[] rooms = match.Value.Rooms.Where(room => room.Optional && !RoomConnections.IsStairway(room)).ToArray();
        if (rooms.Length == 0) return;
        Helper.Input.Suppress(e.Button);
        GameLocation location = Game1.currentLocation;
        HashSet<string> active = EnabledRooms(match.Key, match.Value, location);
        var answers = rooms.Select((room, index) => new Response(index.ToString(), $"{(active.Contains(room.Id) ? "Remove" : "Add")} {room.Name}")).ToList();
        answers.Add(new Response("cancel", "Cancel"));
        location.createQuestionDialogue("Change residence rooms", answers.ToArray(), (farmer, answer) =>
        {
            if (int.TryParse(answer, out int index) && index >= 0 && index < rooms.Length)
                Game1.drawObjectDialogue(ToggleRoom(match.Key, match.Value, location, rooms[index].Id, !active.Contains(rooms[index].Id)));
        });
    }

    private void ListRooms(string command, string[] args)
    {
        if (!Context.IsWorldReady) { Monitor.Log("Load a save first.", LogLevel.Info); return; }
        foreach ((string id, DesignData design) in GetDesigns().Where(pair => pair.Value.Location == Game1.currentLocation.Name))
        {
            HashSet<string> active = EnabledRooms(id, design, Game1.currentLocation);
            foreach (RoomData room in design.Rooms.Where(room => !RoomConnections.IsStairway(room)))
                Monitor.Log($"{design.Location} {room.Id}: {room.Name} ({(active.Contains(room.Id) ? "on" : "off")}, {(room.Optional ? "optional" : "required")})", LogLevel.Info);
        }
    }

    private void RoomCommand(string command, string[] args)
    {
        if (!Context.IsWorldReady || args.Length != 3 || (args[2] != "on" && args[2] != "off"))
        { Monitor.Log("Load a save, then: pixelheart_room <location> <roomId> <on|off>", LogLevel.Info); return; }
        var match = GetDesigns().FirstOrDefault(pair => pair.Value.Location == args[0] && string.IsNullOrEmpty(pair.Value.SpouseNpc));
        GameLocation? location = AllLocations().FirstOrDefault(candidate => candidate.Name == args[0]);
        if (match.Value == null || location == null || !Validate(match.Key, match.Value, out _))
        { Monitor.Log("No valid authored residence matches that location.", LogLevel.Warn); return; }
        Monitor.Log(ToggleRoom(match.Key, match.Value, location, args[1], args[2] == "on"), LogLevel.Info);
    }

    private static bool Validate(string id, DesignData design, out string error)
    {
        error = $"Interior '{id}' has an invalid or unsupported version-1 definition.";
        if (string.IsNullOrWhiteSpace(id) || design == null || design.Version != 1 || design.Width < 1 || design.Height < 1
            || design.Width > 256 || design.Height > 256 || design.Furniture == null || design.Rooms == null || design.Variants == null
            || design.Entry == null || design.SpouseStand == null || design.ProtectedTiles == null
            || (string.IsNullOrWhiteSpace(design.Location) && string.IsNullOrWhiteSpace(design.SpouseNpc))) return false;
        if (design.ProtectedTiles.Any(tile => tile == null || tile.Length != 2 || tile[0] < 0 || tile[1] < 0 || tile[0] >= design.Width || tile[1] >= design.Height)) return false;
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (PlacementData placement in design.Furniture)
            if (placement == null || string.IsNullOrWhiteSpace(placement.Id) || !ids.Add(placement.Id)
                || string.IsNullOrEmpty(placement.ItemId) || !placement.ItemId.StartsWith("(F)", StringComparison.Ordinal) || placement.Rotation < 0 || placement.Rotation > 3
                || placement.X < 0 || placement.Y < 0 || placement.X >= design.Width || placement.Y >= design.Height || placement.ModData == null
                || (placement.HeldItem != null && (string.IsNullOrEmpty(placement.HeldItem.ItemId)
                    || !placement.HeldItem.ItemId.StartsWith("(F)", StringComparison.Ordinal) || placement.HeldItem.ModData == null))) return false;
        ids.Clear();
        foreach (RoomData room in design.Rooms)
            if (room == null || string.IsNullOrWhiteSpace(room.Id) || !ids.Add(room.Id) || room.X < 0 || room.Y < 0
                || room.Width < 1 || room.Height < 1 || room.X + room.Width > design.Width || room.Y + room.Height > design.Height) return false;
        if (design.Rooms.Count(room => room.Optional) > 4 || design.Variants.Count > 16) return false;
        var variantIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (VariantData variant in design.Variants)
            if (variant == null || string.IsNullOrWhiteSpace(variant.Id) || !variantIds.Add(variant.Id)
                || string.IsNullOrEmpty(variant.MapAsset) || !variant.MapAsset.StartsWith("Maps/", StringComparison.Ordinal)
                || variant.EnabledRooms == null || variant.EnabledRooms.Any(room => !ids.Contains(room))
                || variant.EnabledRooms.Distinct(StringComparer.Ordinal).Count() != variant.EnabledRooms.Count
                || design.Rooms.Any(room => !room.Optional && !variant.EnabledRooms.Contains(room.Id))) return false;
        if (design.Variants.Count > 0 && !variantIds.Contains(design.DefaultVariant)) return false;
        if (!RoomConnections.AreValid(design)) return false;
        error = "";
        return true;
    }

    private void Warn(string key, string message)
    {
        if (warnings.Add(key)) Monitor.Log(message, LogLevel.Warn);
    }
}
