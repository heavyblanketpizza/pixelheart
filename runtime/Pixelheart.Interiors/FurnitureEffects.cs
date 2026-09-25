using System.Globalization;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Mods;
using StardewValley.Objects;

namespace Pixelheart.Interiors;

/// <summary>Opt-in appearance for native lamps, following their existing light lifecycle.</summary>
internal sealed class FurnitureEffects
{
    internal const string AssetName = "Pixelheart.Interiors/FurnitureEffects";
    private readonly IModHelper helper;
    private readonly IMonitor monitor;
    private readonly HashSet<string> warnings = new(StringComparer.Ordinal);
    private readonly Dictionary<Furniture, Applied> applied = new();
    private readonly List<(GameLocation Location, Furniture Furniture)> candidates = new();
    private Dictionary<string, Definition>? definitions;
    private bool refresh = true;
    private bool saving;
    private Texture2D? glowTexture;
    private Texture2D? glowSource;

    public sealed class Definition
    {
        public string LightColor { get; set; } = "#FFFFFF";
        public float LightRadius { get; set; } = 2;
        // Authoring pixels (four game pixels each), relative to the placement tile.
        public int[] LightOffsetPixels { get; set; } = new[] { 8, -16 };
        // Horizontal atlas frame indexes; zero is the native unlit frame.
        public int[] LitFrames { get; set; } = new[] { 1 };
        public int FrameMilliseconds { get; set; } = 140;
        public float GlowRadiusPixels { get; set; }
        public float GlowOpacity { get; set; }
    }

    private sealed class LightChange
    {
        internal readonly LightSource Light;
        private readonly Color originalColor;
        private readonly float originalRadius;
        private readonly Vector2 originalPosition;
        private Color color;
        private float radius;
        private Vector2 position;

        internal LightChange(LightSource light)
        {
            Light = light;
            originalColor = light.color.Value;
            originalRadius = light.radius.Value;
            originalPosition = light.position.Value;
        }

        internal void Apply(Color tint, float size, Vector2 center)
        {
            color = tint; radius = size; position = center;
            if (Light.color.Value != color) Light.color.Value = color;
            if (Light.radius.Value != radius) Light.radius.Value = radius;
            if (Light.position.Value != position) Light.position.Value = position;
        }

        internal void Restore()
        {
            // Do not overwrite a later adjustment made by the game or another mod.
            if (Light.color.Value == color) Light.color.Value = originalColor;
            if (Light.radius.Value == radius) Light.radius.Value = originalRadius;
            if (Light.position.Value == position) Light.position.Value = originalPosition;
        }
    }

    private sealed class Applied
    {
        internal Rectangle? Frame;
        internal LightChange? Local;
        internal LightChange? Shared;
    }

    internal FurnitureEffects(IModHelper helper, IMonitor monitor)
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
            if (e.NamesWithoutLocale.Any(name => name.IsEquivalentTo(AssetName)
                || name.IsEquivalentTo("Data/Furniture")))
            {
                Restore();
                definitions = null;
                refresh = true;
            }
        };
        helper.Events.GameLoop.UpdateTicked += Update;
        helper.Events.GameLoop.SaveLoaded += (_, _) => { refresh = true; definitions = null; };
        helper.Events.GameLoop.DayStarted += (_, _) => refresh = true;
        helper.Events.GameLoop.Saving += (_, _) => { saving = true; Restore(); };
        helper.Events.GameLoop.Saved += (_, _) => saving = false;
        helper.Events.GameLoop.ReturnedToTitle += (_, _) =>
        {
            applied.Clear(); candidates.Clear(); definitions = null; warnings.Clear(); refresh = true; saving = false;
            glowTexture?.Dispose(); glowTexture = null; glowSource = null;
        };
        helper.Events.World.FurnitureListChanged += (_, _) => refresh = true;
        helper.Events.Player.Warped += (_, e) => { if (e.IsLocalPlayer) refresh = true; };
        helper.Events.Display.RenderedStep += DrawGlows;
    }

    private Dictionary<string, Definition> GetDefinitions()
    {
        if (definitions != null) return definitions;
        var loaded = helper.GameContent.Load<Dictionary<string, Definition>>(AssetName);
        definitions = new(StringComparer.Ordinal);
        if (loaded.Count > 128) { Warn("count", "Furniture effects exceed the supported limit of 128."); return definitions; }
        foreach ((string id, Definition definition) in loaded)
        {
            if (!id.StartsWith("(F)", StringComparison.Ordinal) || !Valid(definition))
            { Warn(id, $"Furniture effect '{id}' has invalid settings; it was skipped."); continue; }
            definitions[id] = definition;
        }
        return definitions;
    }

    private void RefreshCandidates()
    {
        Restore();
        candidates.Clear();
        var pending = new Stack<GameLocation>(Game1.locations);
        var visited = new HashSet<GameLocation>();
        var configured = GetDefinitions();
        while (pending.Count > 0)
        {
            GameLocation location = pending.Pop();
            if (!visited.Add(location)) continue;
            foreach (Furniture furniture in location.furniture)
                if (configured.ContainsKey(furniture.QualifiedItemId)) candidates.Add((location, furniture));
            foreach (GameLocation interior in location.GetInstancedBuildingInteriors()) pending.Push(interior);
        }
        refresh = false;
    }

    private void Update(object? sender, UpdateTickedEventArgs e)
    {
        if (!Context.IsWorldReady || !Context.IsMainPlayer || saving) return;
        try
        {
            if (refresh) RefreshCandidates();
            double elapsed = Game1.currentGameTime.TotalGameTime.TotalMilliseconds;
            foreach ((GameLocation location, Furniture furniture) in candidates)
            {
                if (!location.furniture.Contains(furniture)) { refresh = true; continue; }
                if (!GetDefinitions().TryGetValue(furniture.QualifiedItemId, out Definition? definition)) continue;
                if (!ApplyTo(furniture, location, definition, elapsed))
                { Warn(furniture.QualifiedItemId, $"Furniture effect '{furniture.QualifiedItemId}' does not fit its native lamp atlas; it was skipped."); continue; }
            }
        }
        catch (Exception ex) { Warn("runtime", $"Furniture effects were deferred: {ex.Message}"); }
    }

    internal bool ApplyTo(Furniture furniture, GameLocation location, Definition definition, double elapsed)
    {
        if (!applied.TryGetValue(furniture, out Applied? state)) applied[furniture] = state = new();
        if (!TryNativeLight(furniture, location, out LightSource? local, out LightSource? shared))
        { RestoreFrame(furniture, state); return true; }
        if (!FramesFit(furniture, definition))
        {
            RestoreFrame(furniture, state);
            state.Local?.Restore(); state.Shared?.Restore();
            state.Local = null; state.Shared = null;
            return false;
        }
        if (state.Local?.Light != local) state.Local = new(local!);
        if (state.Shared?.Light != shared) state.Shared = new(shared!);
        Color tint = NativeLightColor(definition.LightColor);
        Vector2 center = LightPosition(furniture, definition);
        state.Local!.Apply(tint, definition.LightRadius, center);
        state.Shared!.Apply(tint, definition.LightRadius, center);
        Rectangle frame = LitSource(furniture.defaultSourceRect.Value, definition, elapsed);
        if (furniture.sourceRect.Value != frame) furniture.sourceRect.Value = frame;
        state.Frame = frame;
        return true;
    }

    internal static bool Valid(Definition? definition)
        => definition != null && TryColor(definition.LightColor, out _)
            && float.IsFinite(definition.LightRadius) && definition.LightRadius is > 0 and <= 8
            && definition.LightOffsetPixels?.Length == 2 && definition.LightOffsetPixels.All(value => Math.Abs((long)value) <= 128)
            && definition.LitFrames is { Length: > 0 and <= 32 } && definition.LitFrames.All(frame => frame is >= 1 and <= 32)
            && definition.FrameMilliseconds is >= 80 and <= 5000
            && float.IsFinite(definition.GlowRadiusPixels) && definition.GlowRadiusPixels is >= 0 and <= 64
            && float.IsFinite(definition.GlowOpacity) && definition.GlowOpacity is >= 0 and <= 1;

    internal static bool FramesFit(Furniture furniture, Definition definition)
    {
        if (furniture.GetType() != typeof(Furniture) || furniture.furniture_type.Value != Furniture.lamp
            || furniture.rotations.Value != 1 || furniture.currentRotation.Value != 0) return false;
        Rectangle source = furniture.defaultSourceRect.Value;
        Texture2D texture = ItemRegistry.GetDataOrErrorItem(furniture.QualifiedItemId).GetTexture();
        return source.Width > 0 && source.Height > 0 && source.X >= 0 && source.Y >= 0
            && source.Bottom <= texture.Height && (long)source.Right + (long)definition.LitFrames.Max() * source.Width <= texture.Width;
    }

    internal static Rectangle LitSource(Rectangle source, Definition definition, double elapsedMilliseconds)
    {
        int index = (int)(Math.Max(0, elapsedMilliseconds) / definition.FrameMilliseconds % definition.LitFrames.Length);
        // Native addLights supplies its own +one-frame offset during drawing.
        // Only this public source rectangle changes; native geometry stays intact.
        source.X += (definition.LitFrames[index] - 1) * source.Width;
        return source;
    }

    internal static Vector2 LightPosition(Furniture furniture, Definition definition)
        => furniture.TileLocation * 64 + new Vector2(definition.LightOffsetPixels[0], definition.LightOffsetPixels[1]) * 4;

    private static bool TryColor(string? value, out Color color)
    {
        color = Color.White;
        if (value?.Length != 7 || value[0] != '#' || !uint.TryParse(value.AsSpan(1), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out uint rgb)) return false;
        color = new Color((byte)(rgb >> 16), (byte)(rgb >> 8), (byte)rgb);
        return true;
    }

    internal static Color NativeLightColor(string value)
    {
        TryColor(value, out Color color);
        // Stardew subtracts its lightmap; black means full white illumination.
        return new Color(255 - color.R, 255 - color.G, 255 - color.B, 255);
    }

    private static bool TryNativeLight(Furniture furniture, GameLocation location, out LightSource? local, out LightSource? shared)
    {
        local = furniture.lightSource;
        shared = null;
        return furniture.GetType() == typeof(Furniture) && furniture.furniture_type.Value == Furniture.lamp
            && local != null && location.sharedLights.TryGetValue(local.Id, out shared);
    }

    private void DrawGlows(object? sender, RenderedStepEventArgs e)
    {
        if (e.Step != RenderSteps.World_Sorted || !Context.IsWorldReady || Game1.currentLocation == null) return;
        try
        {
            foreach (Furniture furniture in Game1.currentLocation.furniture)
                if (!furniture.isTemporarilyInvisible && GetDefinitions().TryGetValue(furniture.QualifiedItemId, out Definition? definition)
                    && definition.GlowRadiusPixels > 0 && definition.GlowOpacity > 0
                    && TryNativeLight(furniture, Game1.currentLocation, out _, out _) && FramesFit(furniture, definition))
                    DrawGlow(e.SpriteBatch, furniture, definition, GetGlowTexture());
        }
        catch (Exception ex) { Warn("draw", $"Furniture glow was skipped: {ex.Message}"); }
    }

    private Texture2D GetGlowTexture()
    {
        Texture2D source = Game1.sconceLight;
        if (glowTexture != null && !glowTexture.IsDisposed && glowSource == source) return glowTexture;
        glowTexture?.Dispose();
        var pixels = new Color[source.Width * source.Height];
        source.GetData(pixels);
        PremultiplyGlow(pixels);
        glowTexture = new Texture2D(source.GraphicsDevice, source.Width, source.Height);
        glowTexture.SetData(pixels);
        glowSource = source;
        return glowTexture;
    }

    internal static void PremultiplyGlow(Color[] pixels)
    {
        // The native light mask is straight alpha; World_Sorted uses AlphaBlend.
        for (int i = 0; i < pixels.Length; i++)
            pixels[i] = Color.FromNonPremultiplied(pixels[i].R, pixels[i].G, pixels[i].B, pixels[i].A);
    }

    internal static void DrawGlow(SpriteBatch batch, Furniture furniture, Definition definition, Texture2D texture)
    {
        if (texture.IsDisposed) return;
        TryColor(definition.LightColor, out Color color);
        Vector2 position = Game1.GlobalToLocal(Game1.viewport, LightPosition(furniture, definition));
        float scale = definition.GlowRadiusPixels * 8 / texture.Width;
        float depth = (furniture.boundingBox.Value.Bottom - 8) / 10000f + 0.00001f;
        batch.Draw(texture, position, null, color * definition.GlowOpacity, 0,
            new Vector2(texture.Width / 2f, texture.Height / 2f), scale, SpriteEffects.None, depth);
    }

    private static void RestoreFrame(Furniture furniture, Applied state)
    {
        if (state.Frame.HasValue && furniture.sourceRect.Value == state.Frame.Value)
            furniture.sourceRect.Value = furniture.defaultSourceRect.Value;
        state.Frame = null;
    }

    private void Restore()
    {
        if (Context.IsWorldReady && Context.IsMainPlayer)
            RestoreApplied();
        applied.Clear();
    }

    internal void RestoreApplied()
    {
        foreach ((Furniture furniture, Applied state) in applied)
        {
            RestoreFrame(furniture, state);
            state.Local?.Restore();
            state.Shared?.Restore();
        }
        applied.Clear();
    }

    private void Warn(string key, string message)
    {
        if (warnings.Add(key)) monitor.Log(message, LogLevel.Warn);
    }
}
