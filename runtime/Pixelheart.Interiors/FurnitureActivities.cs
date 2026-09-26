using HarmonyLib;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using System.Globalization;
using System.Security.Cryptography;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Locations;
using StardewValley.Mods;
using StardewValley.Objects;
using StardewValley.Pathfinding;

namespace Pixelheart.Interiors;

/// <summary>Optional, short furniture routines for an already idle real NPC.</summary>
internal sealed class FurnitureActivities
{
    internal const string AssetName = "Pixelheart.Interiors/FurnitureActivities";
    private readonly IModHelper helper;
    private readonly IMonitor monitor;
    private readonly HashSet<string> warnings = new(StringComparer.Ordinal);
    private readonly HashSet<string> completed = new(StringComparer.Ordinal);
    private Dictionary<string, Definition>? definitions;
    private Candidate? candidate;
    private Running? active;
    private Journey? journey;
    private Point? returnOrigin;
    private readonly Dictionary<Texture2D, string> textureHashes = new();
    private readonly Dictionary<string, double> retryAfter = new(StringComparer.Ordinal);
    private static FurnitureActivities? instance;
    [ThreadStatic] private static Furniture? drawingSeat;
    private static bool seatDrawingReady;
    private bool paused;
    private int day = -1;

    public sealed class Definition
    {
        public string Npc { get; set; } = "";
        public string FurnitureItemId { get; set; } = "";
        public string SeatItemId { get; set; } = "";
        public int[] SeatOffset { get; set; } = new[] { 1, 1 };
        public int[] ApproachOffset { get; set; } = new[] { -1, 0 };
        public int[] DrawOffsetPixels { get; set; } = new[] { 0, 0 };
        public int FacingDirection { get; set; }
        public int StartTime { get; set; } = 600;
        public int EndTime { get; set; } = 900;
        public int DurationMilliseconds { get; set; } = 12000;
        public bool SpouseOnly { get; set; } = true;
        public List<Frame> Frames { get; set; } = new();
        public MirrorDefinition? MirrorReflection { get; set; }
        // Opt-in. Existing definitions retain their adjacent, rotation-zero behavior.
        public bool SeekFurniture { get; set; }
        public List<string> AllowedLocations { get; set; } = new();
        public Dictionary<int, RotationProfile> Profiles { get; set; } = new();
        [Newtonsoft.Json.JsonIgnore] internal string AppearanceTexture { get; set; } = "";
    }

    public sealed class RotationProfile
    {
        public int[] SeatOffset { get; set; } = new[] { 0, 0 };
        public int[] ApproachOffset { get; set; } = new[] { -1, 0 };
        public int[] DrawOffsetPixels { get; set; } = new[] { 0, 0 };
        public int FacingDirection { get; set; }
        public int SeatRotation { get; set; }
        public string RequiredHeldItemId { get; set; } = "";
        public List<Appearance> Appearances { get; set; } = new();
    }

    public sealed class Appearance
    {
        public string Texture { get; set; } = "";
        public int Width { get; set; }
        public int Height { get; set; }
        // SHA-256 of row-major runtime RGBA bytes (premultiplied, including alpha).
        public string RgbaSha256 { get; set; } = "";
        public List<Frame> Frames { get; set; } = new();
        public MirrorDefinition? MirrorReflection { get; set; }
    }

    /// <summary>A transparent, pre-clipped atlas synchronized to the NPC's actual sprite frame.</summary>
    public sealed class MirrorDefinition
    {
        public string Texture { get; set; } = "";
        public int FrameWidth { get; set; }
        public int FrameHeight { get; set; }
        // Native texture pixels from the furniture sprite's top-left, not its placement tile.
        public int[] OffsetPixels { get; set; } = new[] { 0, 0 };
        public Dictionary<int, int> Frames { get; set; } = new();
    }

    public sealed class Frame
    {
        [Newtonsoft.Json.JsonProperty("Frame")]
        public int Index { get; set; }
        public int Duration { get; set; } = 500;
    }

    private sealed record Station(Furniture Vanity, Furniture? Seat, Vector2 VanityTile,
        Vector2 SeatTile, Vector2 SeatPosition, Point Approach, int VanityRotation, int SeatRotation,
        Definition? Settings = null, string RequiredHeldItemId = "", Stack<Point>? Route = null, int SeatType = -1);
    private sealed record Candidate(string Id, NPC Npc, GameLocation Location, Station Station,
        Vector2 Position, AnimatedSprite Sprite, double Since);
    private sealed record Running(string Id, Definition Definition, NPC Npc, GameLocation Location,
        Station Station, AnimatedSprite Sprite, Texture2D Texture,
        List<FarmerSprite.AnimationFrame> Animation, FarmerSprite.AnimationFrame[] AnimationFrames, Vector2 OriginalPosition,
        Vector2 SeatPosition, int OriginalFacing, Vector2 OriginalOffset, Vector2 AppliedOffset,
        bool OriginalLoop, bool OriginalHideShadow, double Until, Texture2D? ReflectionTexture, Point ReturnOrigin, string? SeatTextureAsset);
    private sealed record Journey(string Id, Definition Definition, NPC Npc, GameLocation Location,
        Station Station, PathFindController Controller, AnimatedSprite Sprite, Texture2D Texture,
        Point Origin, Point Destination, bool Returning, double Until, Vector2 LastPosition, double LastProgress);

    internal FurnitureActivities(IModHelper helper, IMonitor monitor)
    {
        this.helper = helper;
        this.monitor = monitor;
        instance = this;
        helper.Events.Content.AssetRequested += (_, e) =>
        {
            if (e.NameWithoutLocale.IsEquivalentTo(AssetName))
                e.LoadFrom(() => new Dictionary<string, Definition>(), AssetLoadPriority.Low);
        };
        helper.Events.Content.AssetsInvalidated += (_, e) =>
        {
            bool settingsChanged = e.NamesWithoutLocale.Any(name => name.IsEquivalentTo(AssetName));
            string? reflectionAsset = active?.Definition.MirrorReflection?.Texture;
            string? appearanceAsset = active?.Definition.AppearanceTexture ?? journey?.Definition.AppearanceTexture;
            if (settingsChanged || e.NamesWithoutLocale.Any(name =>
                name.IsEquivalentTo("Data/Furniture")
                || (reflectionAsset != null && name.IsEquivalentTo(reflectionAsset))
                || (active?.SeatTextureAsset is { } seatAsset && (name.IsEquivalentTo(seatAsset) || name.IsEquivalentTo(seatAsset + "Front")))
                || (!string.IsNullOrEmpty(appearanceAsset) && name.IsEquivalentTo(appearanceAsset)))) Stop();
            textureHashes.Clear();
            if (settingsChanged) definitions = null;
        };
        helper.Events.Display.RenderedStep += RenderMirror;
        helper.Events.GameLoop.UpdateTicked += Update;
        helper.Events.GameLoop.Saving += (_, _) => Stop();
        helper.Events.GameLoop.DayEnding += (_, _) => Stop();
        helper.Events.GameLoop.SaveLoaded += (_, _) => Reset();
        helper.Events.GameLoop.ReturnedToTitle += (_, _) => Reset();
        helper.Events.Player.Warped += (_, e) => { if (e.IsLocalPlayer) Stop(); };
        helper.ConsoleCommands.Add("pixelheart_activities", "Optional NPC routines: pixelheart_activities [status|pause|resume]", Command);
    }

    // Native furniture already draws correct seat backs/fronts for an occupied
    // seat. Expose that state only while drawing this one NPC's chair, without
    // registering a fake farmer or changing the placed furniture.
    internal static void InstallSeatDrawing(IMonitor monitor)
    {
        if (seatDrawingReady) return;
        try
        {
            var harmony = new Harmony("Pixelheart.Interiors.FurnitureActivities");
            harmony.Patch(AccessTools.Method(typeof(Furniture), nameof(Furniture.draw), new[] { typeof(SpriteBatch), typeof(int), typeof(int), typeof(float) }),
                prefix: new HarmonyMethod(typeof(FurnitureActivities), nameof(BeginSeatDraw)) { priority = Priority.First },
                finalizer: new HarmonyMethod(typeof(FurnitureActivities), nameof(EndSeatDraw)));
            harmony.Patch(AccessTools.Method(typeof(Furniture), nameof(Furniture.HasSittingFarmers)),
                postfix: new HarmonyMethod(typeof(FurnitureActivities), nameof(SeatOccupiedForDraw)));
            seatDrawingReady = true;
        }
        catch (Exception ex)
        {
            monitor.Log($"Furniture seat rendering was unavailable; seated seek routines will be skipped: {ex.Message}", LogLevel.Warn);
        }
    }

    private readonly record struct DrawScope(bool Entered, Furniture? Previous);

    private static void BeginSeatDraw(Furniture __instance, out DrawScope __state)
    {
        __state = new(true, drawingSeat);
        // Clear an outer chair's scope during a nested furniture draw.
        drawingSeat = null;
        if (instance is not { active: { } state } runner || !seatDrawingReady
            || !Furniture.isDrawingLocationFurniture || !state.Definition.SeekFurniture
            || !ReferenceEquals(state.Station.Seat ?? state.Station.Vanity, __instance)
            || !SupportedSeat(__instance) || !WorldAvailable || runner.paused || Interrupted
            || state.Location != Game1.currentLocation) return;
        if (MayContinue(state)) drawingSeat = __instance;
    }

    private static Exception? EndSeatDraw(Exception? __exception, DrawScope __state)
    {
        if (__state.Entered) drawingSeat = __state.Previous;
        return __exception;
    }

    private static void SeatOccupiedForDraw(Furniture __instance, ref bool __result)
    {
        if (ReferenceEquals(drawingSeat, __instance)) __result = true;
    }

    private static bool SupportedSeat(Furniture furniture) => furniture.GetType() == typeof(Furniture)
        && furniture.furniture_type.Value is 0 or 3 && furniture.GetSeatCapacity() == 1;

    private string? PrepareSeatDrawing(string id, Station station)
    {
        Furniture seat = station.Seat ?? station.Vanity;
        if (seat.GetSeatCapacity() == 0) return null;
        if (!seatDrawingReady || !SupportedSeat(seat)) throw new InvalidOperationException("Unsupported native seat renderer.");
        var item = ItemRegistry.GetDataOrErrorItem(seat.QualifiedItemId);
        Texture2D front = helper.GameContent.Load<Texture2D>(item.TextureName + "Front");
        Rectangle source = seat.sourceRect.Value;
        Texture2D texture = item.GetTexture();
        if (item.IsErrorItem || front == null || front.IsDisposed || texture.IsDisposed
            || front.Width != texture.Width || front.Height != texture.Height || source.X < 0 || source.Y < 0
            || source.Right > front.Width || source.Bottom > front.Height)
            throw new InvalidOperationException("The native seat foreground is missing or incompatible.");
        return item.TextureName;
    }

    private void Reset()
    {
        Stop();
        definitions = null;
        completed.Clear();
        warnings.Clear();
        retryAfter.Clear();
        textureHashes.Clear();
        returnOrigin = null;
        day = -1;
    }

    private static double Now => Game1.currentGameTime.TotalGameTime.TotalMilliseconds;
    private static bool WorldAvailable => Context.IsWorldReady && Context.IsMainPlayer && !Context.IsMultiplayer;
    private static bool Interrupted => Game1.eventUp || Game1.dialogueUp || Game1.activeClickableMenu != null
        || Game1.currentLocation?.currentEvent != null || !Context.CanPlayerMove;

    private void Update(object? sender, UpdateTickedEventArgs e)
    {
        try
        {
            if (!WorldAvailable || paused || Interrupted) { Stop(); return; }
            if (day != Game1.Date.TotalDays)
            {
                Stop();
                completed.Clear();
                retryAfter.Clear();
                day = Game1.Date.TotalDays;
            }
            if (active != null)
            {
                if (!MayContinue(active)) Finish();
                return;
            }
            if (journey != null) { UpdateJourney(); return; }
            if (!e.IsMultipleOf(30)) return;
            definitions ??= helper.GameContent.Load<Dictionary<string, Definition>>(AssetName);
            if (definitions.Count > 128)
            {
                Warn("count", "Furniture activity definitions exceed the supported limit of 128.");
                candidate = null;
                return;
            }
            foreach ((string id, Definition definition) in definitions.OrderBy(pair => pair.Key, StringComparer.Ordinal))
            {
                if (string.IsNullOrWhiteSpace(id) || id.Length > 256 || id.Any(char.IsControl) || !Valid(definition))
                { Warn(id, $"Furniture activity '{id}' has invalid settings; it was skipped."); continue; }
                if (completed.Contains(id) || !InWindow(definition)
                    || (retryAfter.TryGetValue(id, out double next) && Now < next)) continue;
                GameLocation location = Game1.currentLocation;
                NPC? npc = location.characters.FirstOrDefault(person => person.Name == definition.Npc && !person.EventActor);
                if (npc == null || !Idle(npc) || npc.Sprite.CurrentAnimation != null
                    || !EligibleLocation(definition, npc, location) || CompletedToday(npc, id)) continue;
                Station? station = definition.SeekFurniture ? FindRoutedStation(definition, npc, location) : FindStation(definition, npc, location);
                Definition settings = station?.Settings ?? definition;
                if (station == null || !FramesFit(settings, npc.Sprite))
                { if (definition.SeekFurniture) retryAfter[id] = Now + 15000; continue; }
                if (candidate == null || candidate.Id != id || candidate.Npc != npc || candidate.Location != location
                    || candidate.Station.Vanity != station.Vanity || candidate.Station.Seat != station.Seat
                    || candidate.Position != npc.Position || candidate.Sprite != npc.Sprite)
                {
                    candidate = new(id, npc, location, station, npc.Position, npc.Sprite, Now);
                    return;
                }
                if (Now - candidate.Since >= 1000)
                {
                    if (definition.SeekFurniture && npc.TilePoint != station.Approach)
                        BeginJourney(id, settings, npc, location, station, station.Route!, npc.TilePoint, station.Approach, false);
                    else Start(id, settings, npc, location, station);
                }
                return;
            }
            candidate = null;
        }
        catch (Exception ex)
        {
            Stop();
            Warn("runtime", $"Furniture routines were deferred: {ex.Message}");
        }
    }

    internal static bool Valid(Definition? data)
    {
        static bool Time(int value) => value >= 600 && value <= 2600 && value % 100 < 60;
        static bool Pair(int[]? value, int maximum) => value?.Length == 2 && value.All(part => Math.Abs((long)part) <= maximum);
        return data != null && !string.IsNullOrWhiteSpace(data.Npc) && data.Npc.Length <= 256
            && data.FurnitureItemId?.StartsWith("(F)", StringComparison.Ordinal) == true
            && (string.IsNullOrEmpty(data.SeatItemId) || data.SeatItemId.StartsWith("(F)", StringComparison.Ordinal))
            && Pair(data.SeatOffset, 8) && Pair(data.ApproachOffset, 1)
            && Math.Abs(data.ApproachOffset[0]) + Math.Abs(data.ApproachOffset[1]) == 1
            && Pair(data.DrawOffsetPixels, 128) && data.FacingDirection is >= 0 and <= 3
            && Time(data.StartTime) && Time(data.EndTime) && data.StartTime < data.EndTime
            && data.DurationMilliseconds is >= 1000 and <= 60000
            && (data.SeekFurniture ? ValidProfiles(data) : ValidFrames(data.Frames) && ValidMirror(data.MirrorReflection, data.Frames));
    }

    internal static bool ValidFrames(List<Frame>? frames) => frames is { Count: > 0 and <= 64 }
        && frames.All(frame => frame != null && frame.Index is >= 0 and <= 4095 && frame.Duration is >= 100 and <= 5000);

    internal static bool ValidProfiles(Definition data)
    {
        static bool Pair(int[]? value, int maximum) => value?.Length == 2 && value.All(part => Math.Abs((long)part) <= maximum);
        return data.AllowedLocations is { Count: <= 16 } && data.AllowedLocations.All(name => !string.IsNullOrWhiteSpace(name) && name.Length <= 256)
            && data.Profiles is { Count: > 0 and <= 4 } && data.Profiles.All(pair => pair.Key is >= 0 and <= 3
                && pair.Value is { } profile && Pair(profile.SeatOffset, 8) && Pair(profile.ApproachOffset, 1)
                && Math.Abs(profile.ApproachOffset[0]) + Math.Abs(profile.ApproachOffset[1]) == 1
                && profile.ApproachOffset[1] == 0 && Pair(profile.DrawOffsetPixels, 128) && profile.FacingDirection is >= 0 and <= 3 && profile.SeatRotation is >= 0 and <= 3
                && (profile.RequiredHeldItemId == "" || profile.RequiredHeldItemId?.StartsWith("(F)", StringComparison.Ordinal) == true)
                && profile.Appearances is { Count: > 0 and <= 8 } && profile.Appearances.All(appearance => appearance != null
                    && !string.IsNullOrWhiteSpace(appearance.Texture) && appearance.Texture.Length <= 512
                    && appearance.Width is >= 16 and <= 4096 && appearance.Height is >= 32 and <= 4096
                    && appearance.RgbaSha256 is { Length: 64 } && appearance.RgbaSha256.All(Uri.IsHexDigit)
                    && ValidFrames(appearance.Frames) && ValidMirror(appearance.MirrorReflection, appearance.Frames)));
    }

    internal static string CompletionKey(string id) => "Pixelheart.Interiors/ActivityDay/" + id;
    internal static bool CompletedToday(NPC npc, string id) => npc.modData.TryGetValue(CompletionKey(id), out string value)
        && value == Game1.Date.TotalDays.ToString(CultureInfo.InvariantCulture);

    internal static bool ValidMirror(MirrorDefinition? mirror, IReadOnlyList<Frame> frames)
        => mirror == null || (!string.IsNullOrWhiteSpace(mirror.Texture) && mirror.Texture.Length <= 512
            && mirror.FrameWidth is >= 1 and <= 256 && mirror.FrameHeight is >= 1 and <= 256
            && mirror.OffsetPixels?.Length == 2 && mirror.OffsetPixels.All(value => Math.Abs((long)value) <= 256)
            && mirror.Frames is { Count: > 0 and <= 64 }
            && mirror.Frames.All(pair => pair.Key is >= 0 and <= 4095 && pair.Value is >= 0 and <= 4095)
            && frames.All(frame => mirror.Frames.ContainsKey(frame.Index)));

    internal static bool MirrorFramesFit(MirrorDefinition mirror, int width, int height)
        => mirror.FrameWidth > 0 && mirror.FrameHeight > 0 && width is > 0 and <= 4096 && height is > 0 and <= 4096
            && width % mirror.FrameWidth == 0 && height % mirror.FrameHeight == 0
            && mirror.Frames != null && mirror.Frames.Values.All(index => index >= 0
                && index < width / mirror.FrameWidth * (height / mirror.FrameHeight));

    internal static Rectangle? MirrorSource(MirrorDefinition mirror, int width, int height, int npcFrame)
    {
        if (!MirrorFramesFit(mirror, width, height) || !mirror.Frames.TryGetValue(npcFrame, out int index)) return null;
        int columns = width / mirror.FrameWidth;
        return new Rectangle(index % columns * mirror.FrameWidth, index / columns * mirror.FrameHeight,
            mirror.FrameWidth, mirror.FrameHeight);
    }

    internal static bool FramesFit(Definition data, AnimatedSprite sprite)
    {
        Texture2D texture = sprite.Texture;
        int width = sprite.SpriteWidth, height = sprite.SpriteHeight;
        if (width <= 0 || height <= 0 || texture.Width % width != 0 || texture.Height % height != 0) return false;
        int count = texture.Width / width * (texture.Height / height);
        return data.Frames.All(frame => frame.Index < count);
    }

    private static bool InWindow(Definition data) => Game1.timeOfDay >= data.StartTime && Game1.timeOfDay < data.EndTime;

    internal static bool Idle(NPC npc) => !npc.isMoving() && npc.controller == null && npc.temporaryController == null
        && !npc.EventActor && !npc.isSleeping.Value && !npc.layingDown && !npc.isInvisible.Value
        && !npc.swimming.Value && !npc.IsEmoting && npc.movementPause == 0 && npc.faceTowardFarmerTimer <= 0
        && !npc.ignoreMovementAnimation && npc.xVelocity == 0 && npc.yVelocity == 0
        && npc.yJumpOffset == 0 && npc.yJumpVelocity == 0
        && !npc.isMovingOnPathFindPath.Value && !npc.doingEndOfRouteAnimation.Value && !npc.goingToDoEndOfRouteAnimation.Value
        && !npc.shouldPlaySpousePatioAnimation.Value && !npc.shouldPlayRobinHammerAnimation.Value
        && (npc.queuedSchedulePaths == null || npc.queuedSchedulePaths.Count == 0)
        && (!npc.followSchedule || npc.Schedule == null || npc.Schedule.Count == 0);

    private static bool EligibleLocation(Definition data, NPC npc, GameLocation location)
    {
        if (npc.currentLocation != location || !location.characters.Contains(npc)) return false;
        if (location is FarmHouse house)
        {
            if (house.OwnerId != Game1.player.UniqueMultiplayerID) return false;
            return (data.SeekFurniture || data.SpouseOnly) ? house.HasNpcSpouseOrRoommate(data.Npc) : true;
        }
        return data.SeekFurniture ? data.AllowedLocations.Contains(location.Name, StringComparer.Ordinal) : !data.SpouseOnly;
    }

    private Definition? ResolveProfile(Definition data, NPC npc, RotationProfile profile)
    {
        Texture2D texture = npc.Sprite.Texture;
        foreach (Appearance appearance in profile.Appearances)
        {
            if (texture.Width != appearance.Width || texture.Height != appearance.Height || texture.IsDisposed) continue;
            if (!string.Equals(npc.Sprite.loadedTexture?.Replace('\\', '/'), appearance.Texture.Replace('\\', '/'), StringComparison.OrdinalIgnoreCase)) continue;
            // Loading the final patched asset proves identity; dimensions alone don't identify an appearance.
            try
            {
                if (!ReferenceEquals(texture, helper.GameContent.Load<Texture2D>(appearance.Texture))) continue;
            }
            catch (Exception ex)
            {
                Warn("appearance:" + appearance.Texture, $"Furniture activity appearance '{appearance.Texture}' was skipped: {ex.Message}");
                continue;
            }
            if (!textureHashes.TryGetValue(texture, out string? hash))
            {
                var colors = new Color[texture.Width * texture.Height];
                texture.GetData(colors);
                hash = HashPixels(colors);
                textureHashes[texture] = hash;
            }
            if (!hash.Equals(appearance.RgbaSha256, StringComparison.OrdinalIgnoreCase)) continue;
            return new Definition
            {
                Npc = data.Npc, FurnitureItemId = data.FurnitureItemId, SeatItemId = data.SeatItemId,
                SeatOffset = profile.SeatOffset, ApproachOffset = profile.ApproachOffset,
                DrawOffsetPixels = profile.DrawOffsetPixels, FacingDirection = profile.FacingDirection,
                StartTime = data.StartTime, EndTime = data.EndTime, DurationMilliseconds = data.DurationMilliseconds,
                SpouseOnly = data.SpouseOnly, SeekFurniture = true, AllowedLocations = data.AllowedLocations,
                Profiles = data.Profiles, Frames = appearance.Frames, MirrorReflection = appearance.MirrorReflection,
                AppearanceTexture = appearance.Texture
            };
        }
        return null;
    }

    internal static string HashPixels(Color[] colors)
    {
        byte[] bytes = new byte[colors.Length * 4];
        for (int i = 0; i < colors.Length; i++)
        { bytes[i * 4] = colors[i].R; bytes[i * 4 + 1] = colors[i].G; bytes[i * 4 + 2] = colors[i].B; bytes[i * 4 + 3] = colors[i].A; }
        return Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    }

    private Station? FindRoutedStation(Definition data, NPC npc, GameLocation location)
    {
        var floor = location.Map?.GetLayer("Back");
        if (floor == null || floor.LayerWidth > 127 || floor.LayerHeight > 127) return null;
        Station? best = null;
        foreach (Furniture furniture in location.furniture.Where(item => item.QualifiedItemId == data.FurnitureItemId)
            .OrderBy(item => Vector2.DistanceSquared(npc.Position, item.TileLocation * 64))
            .ThenBy(item => item.TileLocation.Y).ThenBy(item => item.TileLocation.X).Take(8))
        {
            if (!data.Profiles.TryGetValue(furniture.currentRotation.Value, out RotationProfile? profile)) continue;
            Definition? settings = ResolveProfile(data, npc, profile);
            if (settings == null || !FramesFit(settings, npc.Sprite)) continue;
            Station? station = StationAt(settings, profile, furniture, npc, location);
            if (station == null) continue;
            Stack<Point>? path = FindSafePath(location, npc, station.Approach);
            if (path == null) continue;
            station = station with { Route = path };
            if (best == null || path.Count < best.Route!.Count || (path.Count == best.Route.Count
                && (station.VanityTile.Y < best.VanityTile.Y || (station.VanityTile.Y == best.VanityTile.Y && station.VanityTile.X < best.VanityTile.X)))) best = station;
        }
        return best;
    }

    private static Station? StationAt(Definition data, RotationProfile profile, Furniture furniture, NPC npc, GameLocation location)
    {
        Vector2 tile = furniture.TileLocation + new Vector2(data.SeatOffset[0], data.SeatOffset[1]);
        Furniture? seat = null;
        Vector2 position = tile * 64;
        if (!string.IsNullOrEmpty(data.SeatItemId))
        {
            seat = location.furniture.FirstOrDefault(item => item.QualifiedItemId == data.SeatItemId && item.TileLocation == tile
                && item.currentRotation.Value == profile.SeatRotation);
            if (seat == null || seat.GetSeatCapacity() != 1 || seat.HasSittingFarmers()) return null;
            List<Vector2> seats = seat.GetSeatPositions();
            if (seats.Count != 1) return null;
            position = seats[0] * 64;
        }
        else
        {
            if (furniture.HasSittingFarmers()) return null;
            if (furniture.GetSeatCapacity() == 1)
            {
                List<Vector2> seats = furniture.GetSeatPositions();
                if (seats.Count != 1) return null;
                position = seats[0] * 64;
            }
        }
        Point approach = new((int)tile.X + data.ApproachOffset[0], (int)tile.Y + data.ApproachOffset[1]);
        if (position.Y != approach.Y * 64 || !HeldMatches(furniture, profile.RequiredHeldItemId) || !TileClear(location, approach, npc)
            || !TileClear(location, tile.ToPoint(), npc, seat ?? furniture)) return null;
        return new(furniture, seat, furniture.TileLocation, tile, position, approach,
            furniture.currentRotation.Value, seat?.currentRotation.Value ?? 0, data, profile.RequiredHeldItemId, SeatType: (seat ?? furniture).furniture_type.Value);
    }

    private static bool HeldMatches(Furniture furniture, string required)
        => required.Length == 0 || furniture.heldObject.Value?.QualifiedItemId == required;

    internal static Stack<Point>? FindSafePath(GameLocation location, NPC npc, Point target)
    {
        var floor = location.Map?.GetLayer("Back");
        if (floor == null || floor.LayerWidth > 127 || floor.LayerHeight > 127 || !RouteTileClear(location, target, npc)) return null;
        if (npc.TilePoint == target) return new Stack<Point>();
        if (Math.Abs(npc.TilePoint.X - target.X) + Math.Abs(npc.TilePoint.Y - target.Y) > 48) return null;
        Stack<Point>? path = PathFindController.findPath(npc.TilePoint, target, PathFindController.isAtEndPoint, location, npc, 4096);
        if (path == null || path.Count == 0 || path.Count > 48 || !path.All(tile => RouteTileClear(location, tile, npc))) return null;
        return path;
    }

    private static bool RouteTileClear(GameLocation location, Point tile, NPC npc)
    {
        if (!TileClear(location, tile, npc) || location.warps.Any(warp => warp.X == tile.X && warp.Y == tile.Y)) return false;
        // A route may not execute a map's touch action (warps, events, damage or scripted movement).
        return string.IsNullOrEmpty(location.doesTileHaveProperty(tile.X, tile.Y, "TouchAction", "Back"));
    }

    private void BeginJourney(string id, Definition data, NPC npc, GameLocation location, Station station,
        Stack<Point> route, Point origin, Point destination, bool returning)
    {
        if (!Idle(npc) || npc.Sprite.CurrentAnimation != null || route.Count == 0) return;
        var controller = new PathFindController(new Stack<Point>(route.Reverse()), npc, location)
        { endPoint = destination, finalFacingDirection = data.FacingDirection, NPCSchedule = false, nonDestructivePathing = true };
        journey = new(id, data, npc, location, station, controller, npc.Sprite, npc.Sprite.Texture,
            origin, destination, returning, Now + 15000, npc.Position, Now);
        npc.controller = controller;
        candidate = null;
    }

    private void UpdateJourney()
    {
        Journey state = journey!;
        NPC npc = state.Npc;
        bool arrived = npc.TilePoint == state.Destination && npc.controller == null && !npc.isMoving();
        bool valid = Now < state.Until && Now - state.LastProgress < 3000
            && EligibleLocation(state.Definition, npc, state.Location) && Game1.currentLocation == state.Location
            && InWindow(state.Definition) && npc.Sprite == state.Sprite && npc.Sprite.Texture == state.Texture
            && npc.Sprite.CurrentAnimation == null && npc.temporaryController == null && !npc.EventActor && !npc.isSleeping.Value
            && !npc.layingDown && !npc.isInvisible.Value && !npc.swimming.Value && !npc.IsEmoting
            && npc.movementPause == 0 && npc.faceTowardFarmerTimer <= 0 && !npc.ignoreMovementAnimation
            && !npc.doingEndOfRouteAnimation.Value && !npc.goingToDoEndOfRouteAnimation.Value
            && Unscheduled(npc) && (npc.queuedSchedulePaths == null || npc.queuedSchedulePaths.Count == 0)
            && (ReferenceEquals(npc.controller, state.Controller) || arrived)
            && (state.Returning || StationUnchanged(state.Station, state.Location, npc))
            && state.Controller.pathToEndPoint.All(tile => RouteTileClear(state.Location, tile, npc))
            && Vector2.DistanceSquared(npc.Position, state.LastPosition) <= 64 * 64;
        if (!valid) { Stop(); return; }
        if (arrived)
        {
            journey = null;
            if (!state.Returning && Idle(npc))
            {
                returnOrigin = state.Origin;
                try { Start(state.Id, state.Definition, npc, state.Location, state.Station); }
                finally { returnOrigin = null; }
            }
            return;
        }
        if (npc.Position != state.LastPosition) journey = state with { LastPosition = npc.Position, LastProgress = Now };
    }

    private static bool Unscheduled(NPC npc) => !npc.followSchedule || npc.Schedule == null || npc.Schedule.Count == 0;

    private static Station? FindStation(Definition data, NPC npc, GameLocation location)
    {
        foreach (Furniture vanity in location.furniture.Where(item => item.QualifiedItemId == data.FurnitureItemId))
        {
            if (vanity.currentRotation.Value != 0) continue;
            Vector2 tile = vanity.TileLocation + new Vector2(data.SeatOffset[0], data.SeatOffset[1]);
            Furniture? seat = null;
            Vector2 position = tile * 64;
            if (!string.IsNullOrEmpty(data.SeatItemId))
            {
                seat = location.furniture.FirstOrDefault(item => item.QualifiedItemId == data.SeatItemId && item.TileLocation == tile);
                if (seat == null || seat.currentRotation.Value != 0 || seat.HasSittingFarmers() || seat.GetSeatCapacity() != 1
                    || seat.GetBoundingBox().Width != 64 || seat.GetBoundingBox().Height != 64) continue;
                List<Vector2> seats = seat.GetSeatPositions();
                if (seats.Count != 1) continue;
                position = seats[0] * 64;
            }
            else if (vanity.HasSittingFarmers()) continue;
            if (Vector2.DistanceSquared(npc.Position, position) > 64 * 64) continue;
            var approach = new Point((int)tile.X + data.ApproachOffset[0], (int)tile.Y + data.ApproachOffset[1]);
            if (Vector2.DistanceSquared(npc.Position, new Vector2(approach.X * 64, approach.Y * 64)) > 1
                || !TileClear(location, approach, npc) || !TileClear(location, tile.ToPoint(), npc, seat ?? vanity)) continue;
            return new(vanity, seat, vanity.TileLocation, tile, position, approach, vanity.currentRotation.Value, seat?.currentRotation.Value ?? 0);
        }
        return null;
    }

    private static bool TileClear(GameLocation location, Point tile, NPC npc, Furniture? allowedSeat = null)
    {
        var back = location.Map?.GetLayer("Back");
        if (back == null || tile.X < 0 || tile.Y < 0 || tile.X >= back.LayerWidth || tile.Y >= back.LayerHeight
            || back.Tiles[tile.X, tile.Y] == null || !location.isTilePassable(tile.ToVector2())) return false;
        var bounds = new Rectangle(tile.X * 64, tile.Y * 64, 64, 64);
        return !location.furniture.Any(item => item != allowedSeat && !item.isPassable() && item.GetBoundingBox().Intersects(bounds))
            && !location.characters.Any(person => person != npc && person.GetBoundingBox().Intersects(bounds))
            && !location.farmers.Any(person => person.GetBoundingBox().Intersects(bounds))
            && !location.objects.ContainsKey(tile.ToVector2()) && !location.terrainFeatures.ContainsKey(tile.ToVector2());
    }

    private void Start(string id, Definition data, NPC npc, GameLocation location, Station station)
    {
        // Native body depth follows physical Y, independently of drawOffset. Seek
        // stations use an exact side approach so the native NPC depth stays valid.
        if (data.SeekFurniture && npc.Position.Y != station.SeatPosition.Y)
        {
            candidate = null;
            retryAfter[id] = Now + 15000;
            return;
        }
        string? seatTextureAsset = null;
        if (data.SeekFurniture)
        {
            try { seatTextureAsset = PrepareSeatDrawing(id, station); }
            catch (Exception ex)
            {
                Warn(id + ":seat-render", $"Furniture activity '{id}' was skipped: {ex.Message}");
                candidate = null;
                retryAfter[id] = Now + 15000;
                return;
            }
        }
        AnimatedSprite sprite = npc.Sprite;
        var frames = data.Frames.Select(frame => new FarmerSprite.AnimationFrame(frame.Index, frame.Duration)).ToList();
        // Seek routines remain physically on reachable floor. Only the seated drawing is
        // projected onto its seat; starting/stopping cannot teleport through solid chairs.
        Vector2 position = data.SeekFurniture ? npc.Position : station.SeatPosition;
        Vector2 offset = npc.drawOffset + new Vector2(data.DrawOffsetPixels[0], data.DrawOffsetPixels[1])
            + (data.SeekFurniture ? station.SeatPosition - position : Vector2.Zero);
        active = new(id, data, npc, location, station, sprite, sprite.Texture, frames, frames.ToArray(),
            npc.Position, position, npc.FacingDirection, npc.drawOffset, offset, sprite.loop, npc.hideShadow.Value, Now + data.DurationMilliseconds,
            LoadMirror(id, data.MirrorReflection), returnOrigin ?? npc.TilePoint, seatTextureAsset);
        if (!data.SeekFurniture) npc.Position = position;
        npc.faceDirection(data.FacingDirection);
        npc.drawOffset = offset;
        // A standing shadow would remain on the approach; offsetting it would
        // include the seated pose's vertical adjustment. Hide it while seated.
        if (data.SeekFurniture) npc.hideShadow.Value = true;
        sprite.setCurrentAnimation(frames);
        // The native setter copies into a reusable list. Retain the installed
        // list AND its frames so an external animation replacement is not ours.
        active = active with { Animation = sprite.CurrentAnimation, AnimationFrames = sprite.CurrentAnimation.ToArray() };
        sprite.loop = true;
        completed.Add(id);
        npc.modData[CompletionKey(id)] = Game1.Date.TotalDays.ToString(CultureInfo.InvariantCulture);
        candidate = null;
    }

    private Texture2D? LoadMirror(string id, MirrorDefinition? mirror)
    {
        if (mirror == null) return null;
        try
        {
            Texture2D texture = helper.GameContent.Load<Texture2D>(mirror.Texture);
            if (MirrorFramesFit(mirror, texture.Width, texture.Height)) return texture;
            Warn(id + ":mirror", $"Furniture activity '{id}' has an invalid mirror atlas; its reflection was skipped.");
        }
        catch (Exception ex)
        {
            Warn(id + ":mirror", $"Furniture activity '{id}' couldn't load its mirror reflection: {ex.Message}");
        }
        return null;
    }

    private void RenderMirror(object? sender, RenderedStepEventArgs e)
    {
        // This hook runs inside the game's depth-sorted, PointClamp world batch.
        // RenderedWorld would instead paint over characters and map foreground.
        if (e.Step != RenderSteps.World_Sorted || active is not { ReflectionTexture: { } texture } state
            || !WorldAvailable || paused || Interrupted) return;
        try
        {
            if (!MayContinue(state) || state.Location.shouldHideCharacters()) return;
            DrawMirror(e.SpriteBatch, state.Station.Vanity, state.Definition.MirrorReflection!, texture,
                state.Sprite.CurrentFrame);
        }
        catch (Exception ex)
        {
            Warn(state.Id + ":mirror-draw", $"Furniture activity '{state.Id}' couldn't draw its reflection: {ex.Message}");
        }
    }

    internal static void DrawMirror(SpriteBatch batch, Furniture furniture, MirrorDefinition mirror,
        Texture2D texture, int npcFrame)
    {
        if (texture.IsDisposed || furniture.isTemporarilyInvisible || furniture.shakeTimer > 0
            || furniture.currentRotation.Value != 0) return;
        Rectangle? source = MirrorSource(mirror, texture.Width, texture.Height, npcFrame);
        if (source == null) return;
        Rectangle bounds = furniture.boundingBox.Value;
        // Match Furniture.updateDrawPosition and Furniture.draw, including rugs and
        // furniture types whose native sort line is higher than ordinary furniture.
        var origin = new Vector2(bounds.X, bounds.Bottom - furniture.sourceRect.Height * 4);
        var offset = new Vector2(mirror.OffsetPixels[0], mirror.OffsetPixels[1]) * 4;
        int type = furniture.furniture_type.Value;
        float depth = type == 12 ? 2E-09f + furniture.TileLocation.Y / 100000f
            : (bounds.Bottom - (type is 6 or 17 or 13 ? 48 : 8)) / 10000f;
        batch.Draw(texture, Game1.GlobalToLocal(Game1.viewport, origin + offset), source,
            Color.White, 0, Vector2.Zero, 4, SpriteEffects.None, depth + 0.000001f);
    }

    private static bool OwnAnimation(Running state) => state.Npc.Sprite == state.Sprite
        && ReferenceEquals(state.Sprite.CurrentAnimation, state.Animation)
        && state.Animation.SequenceEqual(state.AnimationFrames);

    private static bool MayContinue(Running state)
    {
        NPC npc = state.Npc;
        Station station = state.Station;
        return Now < state.Until && InWindow(state.Definition) && EligibleLocation(state.Definition, npc, state.Location)
            && Game1.currentLocation == state.Location && OwnAnimation(state) && npc.Sprite.Texture == state.Texture
            && FramesFit(state.Definition, npc.Sprite) && Idle(npc) && npc.Position == state.SeatPosition
            && npc.FacingDirection == state.Definition.FacingDirection && npc.drawOffset == state.AppliedOffset
            && StationUnchanged(station, state.Location, npc)
            && (!state.Definition.SeekFurniture || (npc.hideShadow.Value && TileClear(state.Location, station.Approach, npc)));
    }

    private static bool StationUnchanged(Station station, GameLocation location, NPC npc)
        => location.furniture.Contains(station.Vanity) && !station.Vanity.isTemporarilyInvisible
            && station.Vanity.TileLocation == station.VanityTile
            && station.Vanity.currentRotation.Value == station.VanityRotation
            && (station.Settings == null || station.Vanity.QualifiedItemId == station.Settings.FurnitureItemId)
            && (station.SeatType < 0 || (station.Seat ?? station.Vanity).furniture_type.Value == station.SeatType)
            && HeldMatches(station.Vanity, station.RequiredHeldItemId)
            && (station.Seat == null
                ? !station.Vanity.HasSittingFarmers()
                : location.furniture.Contains(station.Seat) && !station.Seat.isTemporarilyInvisible && station.Seat.TileLocation == station.SeatTile
                    && (station.Settings == null || station.Seat.QualifiedItemId == station.Settings.SeatItemId)
                    && station.Seat.currentRotation.Value == station.SeatRotation && !station.Seat.HasSittingFarmers())
            && TileClear(location, station.SeatTile.ToPoint(), npc, station.Seat ?? station.Vanity);

    private void Finish()
    {
        Running state = active!;
        bool returnToOrigin = state.Definition.SeekFurniture && Now >= state.Until && InWindow(state.Definition)
            && OwnAnimation(state) && Idle(state.Npc) && state.Npc.Position == state.SeatPosition
            && EligibleLocation(state.Definition, state.Npc, state.Location) && StationUnchanged(state.Station, state.Location, state.Npc);
        Stop();
        if (!returnToOrigin || !Idle(state.Npc) || state.Npc.TilePoint == state.ReturnOrigin) return;
        Stack<Point>? path = FindSafePath(state.Location, state.Npc, state.ReturnOrigin);
        if (path != null) BeginJourney(state.Id, state.Definition, state.Npc, state.Location, state.Station,
            path, state.ReturnOrigin, state.ReturnOrigin, true);
    }

    private void Stop()
    {
        candidate = null;
        Journey? travel = journey;
        journey = null;
        if (travel != null)
        {
            retryAfter[travel.Id] = Now + 15000;
            if (ReferenceEquals(travel.Npc.controller, travel.Controller))
            {
                travel.Npc.controller = null;
                // Halt only our ordinary walking. Never clear a replacement animation,
                // event actor's movement, or a controller installed by another system.
                if (travel.Npc.temporaryController == null && travel.Npc.Sprite.CurrentAnimation == null && !travel.Npc.EventActor
                    && !Game1.eventUp && travel.Npc.currentLocation == travel.Location) travel.Npc.Halt();
            }
        }
        Running? state = active;
        active = null;
        if (state == null || !Context.IsWorldReady || !Context.IsMainPlayer) return;
        Restore(state);
    }

    private static void Restore(Running state)
    {
        NPC npc = state.Npc;
        bool ours = OwnAnimation(state);
        // Dialogue may set a facing timer or movement pause while the NPC is
        // still in our exact seat position. Those startup-idle restrictions
        // must not strand the NPC inside a chair during normal conversation.
        bool positionOwned = (ours || npc.Sprite.CurrentAnimation == null) && npc.currentLocation == state.Location && npc.Position == state.SeatPosition
            && !npc.isMoving() && npc.controller == null && npc.temporaryController == null
            && !npc.isMovingOnPathFindPath.Value && !npc.EventActor && !npc.isSleeping.Value
            && !npc.doingEndOfRouteAnimation.Value && !npc.goingToDoEndOfRouteAnimation.Value
            && !Game1.eventUp && state.Location.currentEvent == null;
        if (ours)
        {
            state.Sprite.ClearAnimation();
            state.Sprite.loop = state.OriginalLoop;
            if (npc.FacingDirection == state.Definition.FacingDirection && !Game1.eventUp)
                npc.faceDirection(state.OriginalFacing);
        }
        if (npc.Sprite == state.Sprite && npc.drawOffset == state.AppliedOffset) npc.drawOffset = state.OriginalOffset;
        if (state.Definition.SeekFurniture && npc.hideShadow.Value) npc.hideShadow.Value = state.OriginalHideShadow;
        if (!positionOwned || state.Definition.SeekFurniture) return;
        if (TileClear(state.Location, state.Station.Approach, npc)) npc.Position = state.OriginalPosition;
        else
        {
            // A player can occupy the approach during the routine. Use only an
            // immediately adjacent free floor tile; never restore across rooms.
            Point seat = state.Station.SeatTile.ToPoint();
            foreach (Point point in new[] { new Point(seat.X - 1, seat.Y), new Point(seat.X + 1, seat.Y), new Point(seat.X, seat.Y + 1), new Point(seat.X, seat.Y - 1) })
                if (TileClear(state.Location, point, npc)) { npc.Position = point.ToVector2() * 64; break; }
        }
    }

    private void Command(string command, string[] args)
    {
        string action = args.Length == 0 ? "status" : args[0].ToLowerInvariant();
        if (args.Length > 1 || action is not ("status" or "pause" or "resume"))
        { monitor.Log("Use pixelheart_activities [status|pause|resume].", LogLevel.Info); return; }
        if (action == "pause") { paused = true; Stop(); }
        if (action == "resume") paused = false;
        monitor.Log($"Furniture routines: {(paused ? "paused" : "enabled")}; {(active == null ? "none active" : active.Id)}. "
            + (journey == null ? "" : $"Walking for {journey.Id}. ")
            + "Single-player only; scheduled NPC activity always takes priority.", LogLevel.Info);
    }

    private void Warn(string key, string message)
    {
        if (warnings.Add(key)) monitor.Log(message, LogLevel.Warn);
    }
}
