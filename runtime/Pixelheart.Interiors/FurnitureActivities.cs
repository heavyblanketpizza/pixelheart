using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Locations;
using StardewValley.Mods;
using StardewValley.Objects;

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
        Vector2 SeatTile, Vector2 SeatPosition, Point Approach, int VanityRotation, int SeatRotation);
    private sealed record Candidate(string Id, NPC Npc, GameLocation Location, Station Station,
        Vector2 Position, AnimatedSprite Sprite, double Since);
    private sealed record Running(string Id, Definition Definition, NPC Npc, GameLocation Location,
        Station Station, AnimatedSprite Sprite, Texture2D Texture,
        List<FarmerSprite.AnimationFrame> Animation, FarmerSprite.AnimationFrame[] AnimationFrames, Vector2 OriginalPosition,
        Vector2 SeatPosition, int OriginalFacing, Vector2 OriginalOffset, Vector2 AppliedOffset,
        bool OriginalLoop, double Until, Texture2D? ReflectionTexture);

    internal FurnitureActivities(IModHelper helper, IMonitor monitor)
    {
        this.helper = helper;
        this.monitor = monitor;
        helper.Events.Content.AssetRequested += (_, e) =>
        {
            if (e.NameWithoutLocale.IsEquivalentTo(AssetName))
                e.LoadFrom(() => new Dictionary<string, Definition>(), AssetLoadPriority.Low);
        };
        helper.Events.Content.AssetsInvalidated += (_, e) =>
        {
            bool settingsChanged = e.NamesWithoutLocale.Any(name => name.IsEquivalentTo(AssetName));
            string? reflectionAsset = active?.Definition.MirrorReflection?.Texture;
            if (settingsChanged || e.NamesWithoutLocale.Any(name =>
                name.IsEquivalentTo("Data/Furniture")
                || (reflectionAsset != null && name.IsEquivalentTo(reflectionAsset)))) Stop();
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

    private void Reset()
    {
        Stop();
        definitions = null;
        completed.Clear();
        warnings.Clear();
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
                day = Game1.Date.TotalDays;
            }
            if (active != null)
            {
                if (!MayContinue(active)) Stop();
                return;
            }
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
                if (!Valid(definition)) { Warn(id, $"Furniture activity '{id}' has invalid settings; it was skipped."); continue; }
                if (completed.Contains(id) || !InWindow(definition)) continue;
                GameLocation location = Game1.currentLocation;
                NPC? npc = location.characters.FirstOrDefault(person => person.Name == definition.Npc && !person.EventActor);
                if (npc == null || !Idle(npc) || npc.Sprite.CurrentAnimation != null
                    || !EligibleLocation(definition, npc, location)) continue;
                Station? station = FindStation(definition, npc, location);
                if (station == null || !FramesFit(definition, npc.Sprite)) continue;
                if (candidate == null || candidate.Id != id || candidate.Npc != npc || candidate.Location != location
                    || candidate.Station.Vanity != station.Vanity || candidate.Station.Seat != station.Seat
                    || candidate.Position != npc.Position || candidate.Sprite != npc.Sprite)
                {
                    candidate = new(id, npc, location, station, npc.Position, npc.Sprite, Now);
                    return;
                }
                if (Now - candidate.Since >= 1000) Start(id, definition, npc, location, station);
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
            && data.Frames is { Count: > 0 and <= 64 }
            && data.Frames.All(frame => frame != null && frame.Index is >= 0 and <= 4095 && frame.Duration is >= 100 and <= 5000)
            && ValidMirror(data.MirrorReflection, data.Frames);
    }

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
            return !data.SpouseOnly || house.HasNpcSpouseOrRoommate(data.Npc);
        }
        return !data.SpouseOnly;
    }

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
        AnimatedSprite sprite = npc.Sprite;
        var frames = data.Frames.Select(frame => new FarmerSprite.AnimationFrame(frame.Index, frame.Duration)).ToList();
        Vector2 position = station.SeatPosition;
        Vector2 offset = npc.drawOffset + new Vector2(data.DrawOffsetPixels[0], data.DrawOffsetPixels[1]);
        active = new(id, data, npc, location, station, sprite, sprite.Texture, frames, frames.ToArray(),
            npc.Position, position, npc.FacingDirection, npc.drawOffset, offset, sprite.loop, Now + data.DurationMilliseconds,
            LoadMirror(id, data.MirrorReflection));
        // No controller, schedule, speed, movement lock or saved NPC is created.
        npc.Position = position;
        npc.faceDirection(data.FacingDirection);
        npc.drawOffset = offset;
        sprite.setCurrentAnimation(frames);
        // The native setter copies into a reusable list. Retain the installed
        // list AND its frames so an external animation replacement is not ours.
        active = active with { Animation = sprite.CurrentAnimation, AnimationFrames = sprite.CurrentAnimation.ToArray() };
        sprite.loop = true;
        completed.Add(id);
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
            && state.Location.furniture.Contains(station.Vanity) && station.Vanity.TileLocation == station.VanityTile
            && station.Vanity.currentRotation.Value == station.VanityRotation
            && (station.Seat == null
                ? !station.Vanity.HasSittingFarmers()
                : state.Location.furniture.Contains(station.Seat) && station.Seat.TileLocation == station.SeatTile
                    && station.Seat.currentRotation.Value == station.SeatRotation && !station.Seat.HasSittingFarmers())
            && TileClear(state.Location, station.SeatTile.ToPoint(), npc, station.Seat ?? station.Vanity);
    }

    private void Stop()
    {
        candidate = null;
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
        if (!positionOwned) return;
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
            + "Single-player only; the NPC must already be idle beside the configured seat.", LogLevel.Info);
    }

    private void Warn(string key, string message)
    {
        if (warnings.Add(key)) monitor.Log(message, LogLevel.Warn);
    }
}
