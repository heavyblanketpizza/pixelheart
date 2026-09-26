using System.Globalization;
using System.Reflection;
using System.Reflection.Emit;
using HarmonyLib;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Mods;
using StardewValley.Objects;

namespace Pixelheart.Interiors;

/// <summary>Opt-in lamp and fireplace appearance, following native on/off and light lifecycles.</summary>
internal sealed class FurnitureEffects
{
    internal const string AssetName = "Pixelheart.Interiors/FurnitureEffects";
    private static FurnitureEffects? instance;
    private static bool flameDrawingReady;
    private readonly Dictionary<string, Texture2D?> flameTextures = new(StringComparer.OrdinalIgnoreCase);
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
        public FlameDefinition? Flame { get; set; }
    }

    public sealed class FlameDefinition
    {
        public string Texture { get; set; } = "";
        public int[] FrameSizePixels { get; set; } = new[] { 16, 16 };
        public int[] Frames { get; set; } = new[] { 0 };
        public int FrameMilliseconds { get; set; } = 140;
        public int[] OffsetPixels { get; set; } = new[] { 0, 0 };
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
        instance = this;
        InstallFlameDrawing(monitor);
        helper.Events.Content.AssetRequested += (_, e) =>
        {
            if (e.NameWithoutLocale.IsEquivalentTo(AssetName))
                e.LoadFrom(() => new Dictionary<string, Definition>(), AssetLoadPriority.Low);
        };
        helper.Events.Content.AssetsInvalidated += (_, e) =>
        {
            if (e.NamesWithoutLocale.Any(name => name.IsEquivalentTo(AssetName)
                || name.IsEquivalentTo("Data/Furniture")
                || flameTextures.Keys.Any(asset => name.IsEquivalentTo(asset))))
            {
                Restore();
                flameTextures.Clear();
                definitions = null;
                refresh = true;
            }
        };
        helper.Events.GameLoop.UpdateTicked += Update;
        helper.Events.GameLoop.SaveLoaded += (_, _) => { refresh = true; definitions = null; flameTextures.Clear(); };
        helper.Events.GameLoop.DayStarted += (_, _) => refresh = true;
        helper.Events.GameLoop.Saving += (_, _) => { saving = true; Restore(); };
        helper.Events.GameLoop.Saved += (_, _) => saving = false;
        helper.Events.GameLoop.ReturnedToTitle += (_, _) =>
        {
            applied.Clear(); candidates.Clear(); definitions = null; warnings.Clear(); flameTextures.Clear(); refresh = true; saving = false;
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
                { Warn(furniture.QualifiedItemId, $"Furniture effect '{furniture.QualifiedItemId}' does not fit its native furniture or effect atlas; it was skipped."); continue; }
            }
        }
        catch (Exception ex) { Warn("runtime", $"Furniture effects were deferred: {ex.Message}"); }
    }

    internal bool ApplyTo(Furniture furniture, GameLocation location, Definition definition, double elapsed)
    {
        if (!applied.TryGetValue(furniture, out Applied? state)) applied[furniture] = state = new();
        if (!Valid(definition) || !EffectFits(furniture, definition))
        { RestoreOne(furniture, state); return false; }
        if (!TryNativeLight(furniture, location, out LightSource? local, out LightSource? shared))
        { RestoreOne(furniture, state); return true; }
        if (state.Local?.Light != local) { state.Local?.Restore(); state.Local = new(local!); }
        if (state.Shared?.Light != shared) { state.Shared?.Restore(); state.Shared = new(shared!); }
        Color tint = NativeLightColor(definition.LightColor);
        Vector2 center = LightPosition(furniture, definition);
        state.Local!.Apply(tint, definition.LightRadius, center);
        state.Shared!.Apply(tint, definition.LightRadius, center);
        if (definition.Flame == null)
        {
            Rectangle frame = LitSource(furniture.defaultSourceRect.Value, definition, elapsed);
            if (furniture.sourceRect.Value != frame) furniture.sourceRect.Value = frame;
            state.Frame = frame;
        }
        else RestoreFrame(furniture, state);
        return true;
    }

    internal static bool Valid(Definition? definition)
        => definition != null && TryColor(definition.LightColor, out _)
            && float.IsFinite(definition.LightRadius) && definition.LightRadius is > 0 and <= 8
            && definition.LightOffsetPixels?.Length == 2 && definition.LightOffsetPixels.All(value => Math.Abs((long)value) <= 128)
            && definition.LitFrames is { Length: > 0 and <= 32 } && definition.LitFrames.All(frame => frame is >= 1 and <= 32)
            && definition.FrameMilliseconds is >= 80 and <= 5000
            && float.IsFinite(definition.GlowRadiusPixels) && definition.GlowRadiusPixels is >= 0 and <= 64
            && float.IsFinite(definition.GlowOpacity) && definition.GlowOpacity is >= 0 and <= 1
            && (definition.Flame == null || ValidFlame(definition.Flame));

    internal static bool ValidFlame(FlameDefinition? flame)
        => flame != null && !string.IsNullOrWhiteSpace(flame.Texture) && flame.Texture.Length <= 256
            && !flame.Texture.Contains(':') && !flame.Texture.StartsWith('/') && !flame.Texture.StartsWith('\\')
            && !flame.Texture.Replace('\\', '/').Split('/').Any(part => part is ".." or "." or "")
            && flame.FrameSizePixels is { Length: 2 } && flame.FrameSizePixels.All(size => size is > 0 and <= 64)
            && flame.Frames is { Length: > 0 and <= 32 } && flame.Frames.All(frame => frame is >= 0 and <= 63)
            && flame.FrameMilliseconds is >= 80 and <= 5000
            && flame.OffsetPixels is { Length: 2 } && flame.OffsetPixels.All(value => Math.Abs((long)value) <= 128);

    internal static bool FramesFit(Furniture furniture, Definition definition)
    {
        if (definition.Flame != null || furniture.GetType() != typeof(Furniture) || furniture.furniture_type.Value != Furniture.lamp
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

    private bool EffectFits(Furniture furniture, Definition definition)
        => definition.Flame == null ? FramesFit(furniture, definition)
            : flameDrawingReady && SupportedFireplace(furniture) && GetFlameTexture(definition.Flame) != null;

    internal static bool SupportedFireplace(Furniture furniture)
        => furniture.GetType() == typeof(Furniture) && furniture.furniture_type.Value == Furniture.fireplace
            && furniture.rotations.Value == 1 && furniture.currentRotation.Value == 0 && !furniture.flipped.Value;

    private Texture2D? GetFlameTexture(FlameDefinition flame)
    {
        if (!ValidFlame(flame)) return null;
        if (!flameTextures.TryGetValue(flame.Texture, out Texture2D? texture))
        {
            try { texture = helper.GameContent.Load<Texture2D>(flame.Texture); }
            catch (Exception ex) { Warn("flame:" + flame.Texture, $"Furniture flame '{flame.Texture}' was unavailable; native fire was kept: {ex.Message}"); }
            flameTextures[flame.Texture] = texture;
        }
        return texture != null && !texture.IsDisposed && texture.Height == flame.FrameSizePixels[1]
            && (long)(flame.Frames.Max() + 1) * flame.FrameSizePixels[0] <= texture.Width ? texture : null;
    }

    internal static Rectangle FlameSource(FlameDefinition flame, double elapsedMilliseconds)
    {
        if (!double.IsFinite(elapsedMilliseconds)) elapsedMilliseconds = 0;
        int index = (int)(Math.Max(0, elapsedMilliseconds) / flame.FrameMilliseconds % flame.Frames.Length);
        return new Rectangle(flame.Frames[index] * flame.FrameSizePixels[0], 0, flame.FrameSizePixels[0], flame.FrameSizePixels[1]);
    }

    internal static void InstallFlameDrawing(IMonitor monitor)
    {
        if (flameDrawingReady) return;
        var harmony = new Harmony("Pixelheart.Interiors.FurnitureEffects");
        try
        {
            harmony.Patch(AccessTools.Method(typeof(Furniture), nameof(Furniture.draw),
                    new[] { typeof(SpriteBatch), typeof(int), typeof(int), typeof(float) }),
                transpiler: new HarmonyMethod(typeof(FurnitureEffects), nameof(ReplaceFlameCalls)));
            flameDrawingReady = true;
        }
        catch (Exception ex)
        {
            harmony.UnpatchAll(harmony.Id);
            monitor.Log($"Custom fireplace effects were unavailable; native fire and light were kept: {ex.Message}", LogLevel.Warn);
        }
    }

    private static readonly MethodInfo NativeDraw = AccessTools.Method(typeof(SpriteBatch), nameof(SpriteBatch.Draw),
        new[] { typeof(Texture2D), typeof(Vector2), typeof(Rectangle?), typeof(Color), typeof(float),
            typeof(Vector2), typeof(float), typeof(SpriteEffects), typeof(float) });

    internal static IEnumerable<CodeInstruction> ReplaceFlameCalls(IEnumerable<CodeInstruction> instructions)
    {
        var code = instructions.Select(instruction => new CodeInstruction(instruction)).ToList();
        FieldInfo type = AccessTools.Field(typeof(Furniture), nameof(Furniture.furniture_type));
        FieldInfo on = AccessTools.Field(typeof(StardewValley.Object), nameof(StardewValley.Object.isOn));
        FieldInfo cursors = AccessTools.Field(typeof(Game1), nameof(Game1.mouseCursors));
        var regions = new List<(int Start, int End)>();
        for (int i = 4; i + 3 < code.Count; i++)
        {
            if (!code[i].LoadsField(type) || !code[i + 2].LoadsConstant(Furniture.fireplace)
                || code[i + 3].opcode != OpCodes.Bne_Un && code[i + 3].opcode != OpCodes.Bne_Un_S
                || !code[i - 4].LoadsField(on)
                || code[i - 2].opcode != OpCodes.Brfalse && code[i - 2].opcode != OpCodes.Brfalse_S
                || code[i - 2].operand is not Label offLabel
                || code[i + 3].operand is not Label endLabel) continue;
            int end = code.FindIndex(i + 4, instruction => instruction.labels.Contains(endLabel));
            if (end > i && code[end].labels.Contains(offLabel)) regions.Add((i + 4, end));
        }
        if (regions.Count != 1) throw new InvalidOperationException("Native fireplace branch did not match the supported renderer.");
        var (start, stop) = regions[0];
        var region = code.GetRange(start, stop - start);
        var draws = Enumerable.Range(start, stop - start).Where(index => code[index].Calls(NativeDraw)).ToArray();
        // Do not partly patch a changed renderer or the separate torch path.
        if (draws.Length != 2 || region.Count(instruction => instruction.LoadsField(cursors)) != 2
            || region.Count(instruction => instruction.LoadsConstant(276)) != 2
            || region.Count(instruction => instruction.LoadsConstant(1985)) != 2
            || draws.Any(index => code[index].blocks.Count != 0))
            throw new InvalidOperationException("Native fireplace flame calls did not match the supported renderer.");
        MethodInfo replacement = AccessTools.Method(typeof(FurnitureEffects), nameof(DrawFireplaceFlame));
        for (int part = draws.Length - 1; part >= 0; part--)
        {
            int index = draws[part];
            var load = new CodeInstruction(OpCodes.Ldarg_0);
            load.labels.AddRange(code[index].labels);
            code[index].labels.Clear();
            code.InsertRange(index, new[] { load, new CodeInstruction(OpCodes.Ldc_I4, part), new CodeInstruction(OpCodes.Ldarg_S, (byte)4) });
            code[index + 3] = new CodeInstruction(OpCodes.Call, replacement);
        }
        return code;
    }

    private static void DrawFireplaceFlame(SpriteBatch batch, Texture2D texture, Vector2 position,
        Rectangle? source, Color color, float rotation, Vector2 origin, float scale, SpriteEffects effects,
        float depth, Furniture furniture, int part, float alpha)
    {
        Texture2D? custom = null;
        FlameDefinition? flame = null;
        FurnitureEffects? runner = instance;
        if (flameDrawingReady && runner != null && !runner.saving && Furniture.isDrawingLocationFurniture
            && !furniture.isTemporarilyInvisible && furniture.isOn.Value && SupportedFireplace(furniture))
        {
            try
            {
                if (runner.GetDefinitions().TryGetValue(furniture.QualifiedItemId, out Definition? definition)
                    && definition.Flame is { } configured)
                { custom = runner.GetFlameTexture(configured); flame = configured; }
            }
            catch (Exception ex) { runner.Warn("flame-draw", $"Custom fireplace drawing was deferred; native fire was kept: {ex.Message}"); }
        }
        if (custom == null || flame == null)
        {
            batch.Draw(texture, position, source, color, rotation, origin, scale, effects, depth);
            return;
        }
        if (part != 0) return;
        Vector2 world = furniture.TileLocation * 64 + new Vector2(flame.OffsetPixels[0], flame.OffsetPixels[1]) * 4;
        batch.Draw(custom, Game1.GlobalToLocal(Game1.viewport, world),
            FlameSource(flame, Game1.currentGameTime.TotalGameTime.TotalMilliseconds), Color.White * alpha,
            0, Vector2.Zero, 4, SpriteEffects.None, depth);
    }

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
        return furniture.GetType() == typeof(Furniture)
            && (furniture.furniture_type.Value == Furniture.lamp || furniture.furniture_type.Value == Furniture.fireplace && furniture.isOn.Value)
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
                    && TryNativeLight(furniture, Game1.currentLocation, out _, out _) && EffectFits(furniture, definition))
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

    private static void RestoreOne(Furniture furniture, Applied state)
    {
        RestoreFrame(furniture, state);
        state.Local?.Restore(); state.Shared?.Restore();
        state.Local = null; state.Shared = null;
    }

    internal void RestoreApplied()
    {
        foreach ((Furniture furniture, Applied state) in applied)
        {
            RestoreOne(furniture, state);
        }
        applied.Clear();
    }

    private void Warn(string key, string message)
    {
        if (warnings.Add(key)) monitor.Log(message, LogLevel.Warn);
    }
}
