using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Menus;
using StardewValley.Objects;

namespace Pixelheart.Interiors;

/// <summary>Connects ordinary, save-compatible furniture to a native data shop.</summary>
internal sealed class FurnitureCatalogues
{
    internal const string AssetName = "Pixelheart.Interiors/FurnitureCatalogues";
    private readonly IModHelper helper;
    private readonly IMonitor monitor;
    private readonly HashSet<string> warnings = new(StringComparer.Ordinal);
    private Dictionary<string, Definition>? definitions;

    public sealed class Definition
    {
        public string ShopId { get; set; } = "";
    }

    internal FurnitureCatalogues(IModHelper helper, IMonitor monitor)
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
            if (e.NamesWithoutLocale.Any(name => name.IsEquivalentTo(AssetName))) definitions = null;
        };
        helper.Events.GameLoop.ReturnedToTitle += (_, _) => { definitions = null; warnings.Clear(); };
        helper.Events.Input.ButtonPressed += OnButtonPressed;
    }

    internal static bool Valid(string itemId, Definition? data) => itemId.StartsWith("(F)", StringComparison.Ordinal)
        && itemId.Length is > 3 and <= 259 && !itemId.Any(char.IsControl)
        && data != null && !string.IsNullOrWhiteSpace(data.ShopId) && data.ShopId.Length <= 256 && !data.ShopId.Any(char.IsControl);

    private void OnButtonPressed(object? sender, ButtonPressedEventArgs e)
    {
        if (!Context.IsWorldReady || !Context.IsPlayerFree || !e.Button.IsActionButton()
            || helper.Input.IsSuppressed(e.Button) || Game1.eventUp || Game1.currentLocation == null) return;
        try
        {
            definitions ??= helper.GameContent.Load<Dictionary<string, Definition>>(AssetName);
            if (definitions.Count > 128) { Warn("count", "Furniture catalogue definitions exceed the supported limit of 128."); return; }
            Furniture? furniture = Game1.currentLocation.GetFurnitureAt(e.Cursor.GrabTile);
            if (furniture == null || !definitions.TryGetValue(furniture.QualifiedItemId, out Definition? definition)) return;
            if (!Valid(furniture.QualifiedItemId, definition)) { Warn(furniture.QualifiedItemId, "An invalid furniture catalogue definition was skipped."); return; }
            if (!furniture.IsCloseEnoughToFarmer(Game1.player) || furniture.isTemporarilyInvisible) return;
            if (!Utility.TryOpenShopMenu(definition.ShopId, null, true)) return;
            if (Game1.activeClickableMenu is ShopMenu menu) menu.UseFurnitureCatalogueTabs();
            helper.Input.Suppress(e.Button);
        }
        catch (Exception ex) { Warn("open", $"Furniture catalogue could not be opened: {ex.Message}"); }
    }

    private void Warn(string key, string message)
    {
        if (warnings.Add(key)) monitor.Log(message, LogLevel.Warn);
    }
}
