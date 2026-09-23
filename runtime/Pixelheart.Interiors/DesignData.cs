using Newtonsoft.Json;

namespace Pixelheart.Interiors;

public sealed class DesignData
{
    [JsonProperty("version")] public int Version { get; set; }
    [JsonProperty("location")] public string Location { get; set; } = "";
    [JsonProperty("spouse_npc")] public string? SpouseNpc { get; set; }
    [JsonProperty("width")] public int Width { get; set; }
    [JsonProperty("height")] public int Height { get; set; }
    [JsonProperty("entry")] public int[] Entry { get; set; } = Array.Empty<int>();
    [JsonProperty("spouse_stand")] public int[] SpouseStand { get; set; } = Array.Empty<int>();
    [JsonProperty("protected_tiles")] public List<int[]> ProtectedTiles { get; set; } = new();
    [JsonProperty("spouse_marker_x")] public int SpouseMarkerX { get; set; }
    [JsonProperty("spouse_marker_y")] public int SpouseMarkerY { get; set; }
    [JsonProperty("furniture")] public List<PlacementData> Furniture { get; set; } = new();
    [JsonProperty("rooms")] public List<RoomData> Rooms { get; set; } = new();
    [JsonProperty("variants")] public List<VariantData> Variants { get; set; } = new();
    [JsonProperty("default_variant")] public string DefaultVariant { get; set; } = "";
}

public sealed class PlacementData
{
    [JsonProperty("id")] public string Id { get; set; } = "";
    [JsonProperty("item_id")] public string ItemId { get; set; } = "";
    [JsonProperty("x")] public int X { get; set; }
    [JsonProperty("y")] public int Y { get; set; }
    [JsonProperty("rotation")] public int Rotation { get; set; }
    [JsonProperty("mod_data")] public Dictionary<string, string> ModData { get; set; } = new();
}

public sealed class RoomData
{
    [JsonProperty("id")] public string Id { get; set; } = "";
    [JsonProperty("name")] public string Name { get; set; } = "";
    [JsonProperty("x")] public int X { get; set; }
    [JsonProperty("y")] public int Y { get; set; }
    [JsonProperty("width")] public int Width { get; set; }
    [JsonProperty("height")] public int Height { get; set; }
    [JsonProperty("optional")] public bool Optional { get; set; }
    [JsonProperty("enabled")] public bool Enabled { get; set; } = true;
    [JsonProperty("kind")] public string Kind { get; set; } = "";
    [JsonProperty("level")] public int Level { get; set; }
    [JsonProperty("upper_room_id")] public string UpperRoomId { get; set; } = "";
    [JsonProperty("lower_room_id")] public string LowerRoomId { get; set; } = "";
}

public sealed class VariantData
{
    [JsonProperty("id")] public string Id { get; set; } = "";
    [JsonProperty("map_asset")] public string MapAsset { get; set; } = "";
    [JsonProperty("enabled_rooms")] public List<string> EnabledRooms { get; set; } = new();
}
