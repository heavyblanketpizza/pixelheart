using HarmonyLib;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;

namespace Pixelheart.Interiors;

/// <summary>Optional wide artwork for an actual spouse-patio activity; never changes the NPC or their schedule.</summary>
internal sealed class PatioPoses
{
    internal const string AssetName = "Pixelheart.Interiors/PatioPoses";
    private readonly IModHelper helper;
    private readonly IMonitor monitor;
    private readonly HashSet<string> warnings = new(StringComparer.Ordinal);
    private readonly Dictionary<string, Texture2D?> textures = new(StringComparer.OrdinalIgnoreCase);
    private Dictionary<string, Definition>? definitions;
    private bool definitionsFailed;
    private static PatioPoses? instance;

    public sealed class Definition
    {
        public string Npc { get; set; } = "";
        public string Texture { get; set; } = "";
        public int FrameWidth { get; set; } = 32;
        public int FrameHeight { get; set; } = 32;
        public List<FrameData> Frames { get; set; } = new();
        // Native pixels relative to the top-left of the ordinary NPC body draw.
        // The game's own SpriteAnimationPixelOffset is already included there.
        public int[] DrawOffsetPixels { get; set; } = new[] { 0, 0 };
    }

    public sealed class FrameData
    {
        public int Frame { get; set; }
        public int Duration { get; set; } = 500;
    }

    internal PatioPoses(IModHelper helper, IMonitor monitor)
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
            // Definitions can refer to new textures after a Content Patcher reload.
            // Clear failed entries too so a repaired asset is usable immediately.
            if (e.NamesWithoutLocale.Any(name => name.IsEquivalentTo(AssetName)
                || textures.Keys.Any(key => name.IsEquivalentTo(key)))) Reset();
        };
        helper.Events.GameLoop.ReturnedToTitle += (_, _) => Reset();
        try
        {
            var draw = AccessTools.DeclaredMethod(typeof(NPC), nameof(NPC.draw), new[] { typeof(SpriteBatch), typeof(float) });
            var shadow = AccessTools.DeclaredMethod(typeof(Character), nameof(Character.DrawShadow), new[] { typeof(SpriteBatch) });
            if (draw == null || shadow == null) throw new MissingMethodException("The supported NPC draw methods were not found.");
            var harmony = new Harmony("Pixelheart.Interiors.PatioPoses");
            harmony.Patch(draw, prefix: new HarmonyMethod(typeof(PatioPoses), nameof(DrawPose)) { priority = Priority.Last });
            try
            {
                harmony.Patch(shadow, prefix: new HarmonyMethod(typeof(PatioPoses), nameof(DrawShadow)) { priority = Priority.Last });
            }
            catch
            {
                harmony.Unpatch(draw, HarmonyPatchType.Prefix, harmony.Id);
                throw;
            }
        }
        catch (Exception ex)
        {
            Warn("patch", $"Wide spouse-patio poses are unavailable; normal NPC drawing is unchanged: {ex.Message}");
        }
    }

    private void Reset()
    {
        definitions = null;
        definitionsFailed = false;
        textures.Clear();
        warnings.Clear();
    }

    internal static bool Valid(string id, Definition? value) => value != null
        && !string.IsNullOrWhiteSpace(id) && id.Length <= 256 && !id.Any(char.IsControl)
        && string.Equals(id, value.Npc, StringComparison.Ordinal)
        && !string.IsNullOrWhiteSpace(value.Texture) && value.Texture.Length <= 512 && !value.Texture.Any(char.IsControl)
        && value.FrameWidth is >= 16 and <= 128 && value.FrameWidth % 16 == 0
        && value.FrameHeight is >= 16 and <= 128 && value.FrameHeight % 16 == 0
        && value.DrawOffsetPixels is { Length: 2 } && value.DrawOffsetPixels.All(part => Math.Abs((long)part) <= 128)
        && value.Frames is { Count: > 0 and <= 64 }
        && value.Frames.All(frame => frame != null && frame.Frame is >= 0 and <= 4095 && frame.Duration is >= 1 and <= 60000);

    internal static bool FramesFit(Definition value, int width, int height) => width is > 0 and <= 4096
        && height is > 0 and <= 4096 && value.FrameWidth > 0 && value.FrameHeight > 0
        && width % value.FrameWidth == 0 && height % value.FrameHeight == 0
        && value.Frames.All(frame => frame.Frame < width / value.FrameWidth * (height / value.FrameHeight));

    internal static Rectangle FrameSource(Definition value, int textureWidth, double milliseconds)
    {
        long cycle = value.Frames.Sum(frame => (long)frame.Duration);
        double phase = Math.Max(0, milliseconds) % cycle;
        int index = value.Frames[^1].Frame;
        foreach (FrameData frame in value.Frames)
        {
            if (phase < frame.Duration) { index = frame.Frame; break; }
            phase -= frame.Duration;
        }
        int columns = textureWidth / value.FrameWidth;
        return new Rectangle(index % columns * value.FrameWidth, index / columns * value.FrameHeight,
            value.FrameWidth, value.FrameHeight);
    }

    internal static bool AtPatioSpot(Vector2 position, Point spot) =>
        Vector2.DistanceSquared(position, new Vector2(spot.X * 64, spot.Y * 64)) < 0.25f;

    internal static bool MatchesPatioAnimation(NPC npc)
    {
        var expected = npc.GetData()?.SpousePatio?.SpriteAnimationFrames;
        var playing = npc.Sprite.CurrentAnimation;
        if (expected is not { Count: > 0 } || playing == null || playing.Count != expected.Count) return false;
        for (int index = 0; index < expected.Count; index++)
        {
            int[] frame = expected[index];
            if (frame is not { Length: > 0 } || playing[index].frame != frame[0]
                || playing[index].milliseconds != (frame.Length > 1 ? frame[1] : 100)) return false;
        }
        return true;
    }

    // Gate each draw against this screen's location and replicated game state. No
    // cached farmer, location, actor or animation is shared between split screens.
    private static bool Eligible(NPC npc) => Context.IsWorldReady
        && npc.currentLocation is Farm farm && ReferenceEquals(farm, Game1.currentLocation)
        && ReferenceEquals(farm, Game1.getFarm()) && farm.characters.Contains(npc)
        && npc.shouldPlaySpousePatioAnimation.Value && MatchesPatioAnimation(npc)
        && Game1.MasterPlayer is { } owner && owner.isMarriedOrRoommates() && owner.spouse == npc.Name
        && AtPatioSpot(npc.Position, farm.spousePatioSpot)
        && !npc.EventActor && !Game1.eventUp && farm.currentEvent == null
        && !npc.IsInvisible && !npc.swimming.Value && !npc.isSleeping.Value && !npc.layingDown
        && !npc.isMoving() && npc.controller == null && npc.temporaryController == null
        && npc.yJumpOffset == 0 && npc.rotation == 0 && npc.shakeTimer <= 0 && npc.scale.Value == 1;

    private bool TryResolve(NPC npc, out Definition value, out Texture2D texture)
    {
        value = null!;
        texture = null!;
        if (!Eligible(npc) || definitionsFailed) return false;
        if (definitions == null)
        {
            try
            {
                definitions = helper.GameContent.Load<Dictionary<string, Definition>>(AssetName);
                if (definitions == null || definitions.Count > 128) throw new InvalidDataException("At most 128 pose definitions are supported.");
            }
            catch (Exception ex)
            {
                definitionsFailed = true;
                Warn("definitions", $"Spouse-patio poses could not be loaded; normal NPC drawing is unchanged: {ex.Message}");
                return false;
            }
        }
        if (!definitions.TryGetValue(npc.Name, out Definition? definition)) return false;
        if (!Valid(npc.Name, definition))
        {
            Warn(npc.Name, $"Spouse-patio pose '{npc.Name}' has invalid settings; normal NPC drawing is unchanged.");
            return false;
        }
        value = definition!;
        if (!textures.TryGetValue(value.Texture, out Texture2D? loaded))
        {
            try { loaded = helper.GameContent.Load<Texture2D>(value.Texture); }
            catch (Exception ex) { Warn(value.Texture, $"Spouse-patio pose texture '{value.Texture}' could not be loaded: {ex.Message}"); }
            textures[value.Texture] = loaded;
        }
        if (loaded == null)
        {
            Warn(value.Texture, $"Spouse-patio pose texture '{value.Texture}' is unavailable; normal NPC drawing is unchanged.");
            return false;
        }
        if (loaded.IsDisposed || !FramesFit(value, loaded.Width, loaded.Height))
        {
            Warn(npc.Name + ":texture", $"Spouse-patio pose '{npc.Name}' has no compatible texture; normal NPC drawing is unchanged.");
            return false;
        }
        texture = loaded;
        return true;
    }

    private static bool DrawPose(NPC __instance, SpriteBatch b, float alpha, bool __runOriginal)
    {
        // Respect an earlier mod's explicit replacement instead of drawing twice.
        if (!__runOriginal || instance == null) return true;
        bool drawn = false;
        try
        {
            if (!instance.TryResolve(__instance, out Definition value, out Texture2D texture)) return true;
            NPC npc = __instance;
            DrawBody(npc, value, texture, b, alpha);
            drawn = true;
            // Breathing/glow use the standing sheet and would duplicate body pixels.
            // Emotes stay attached to the real NPC and retain normal interaction.
            npc.DrawEmote(b);
            return false;
        }
        catch (Exception ex)
        {
            instance.Warn(__instance.Name + ":draw", $"Spouse-patio {(drawn ? "emote" : "pose")} drawing failed: {ex.Message}");
            // An emote failure after the body was submitted must not draw two NPCs.
            return !drawn;
        }
    }

    internal static void DrawBody(NPC npc, Definition value, Texture2D texture, SpriteBatch batch, float alpha)
    {
        // Matches the installed game's unrotated, scale-one NPC body origin.
        Vector2 topLeft = npc.getLocalPosition(Game1.viewport)
            + new Vector2(npc.GetSpriteWidthForPositioning() * 2, npc.GetBoundingBox().Height / 2)
            - new Vector2(npc.Sprite.SpriteWidth * 2, npc.Sprite.SpriteHeight * 3)
            + new Vector2(value.DrawOffsetPixels[0] * 4, value.DrawOffsetPixels[1] * 4);
        float depth = Math.Max(0, npc.drawOnTop ? 0.991f : npc.StandingPixel.Y / 10000f);
        Rectangle source = FrameSource(value, texture.Width, Game1.currentGameTime.TotalGameTime.TotalMilliseconds);
        batch.Draw(texture, topLeft, source, Color.White * alpha, 0, Vector2.Zero, 4, SpriteEffects.None, depth);
    }

    private static bool DrawShadow(Character __instance, bool __runOriginal)
    {
        if (!__runOriginal || __instance is not NPC npc || instance == null) return true;
        try { return !instance.TryResolve(npc, out _, out _); }
        catch (Exception ex)
        {
            instance.Warn(npc.Name + ":shadow", $"Spouse-patio shadow check failed; normal shadow drawing is unchanged: {ex.Message}");
            return true;
        }
    }

    private void Warn(string id, string message)
    {
        if (warnings.Add(id)) monitor.Log(message, LogLevel.Warn);
    }
}
