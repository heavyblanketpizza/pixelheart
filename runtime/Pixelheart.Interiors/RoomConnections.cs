namespace Pixelheart.Interiors;

/// <summary>A raised room and its stair passage are one optional expansion.</summary>
internal static class RoomConnections
{
    internal static bool IsStairway(RoomData room) => room.Kind == "stairway";

    internal static RoomData[] ToggleGroup(DesignData design, RoomData room) =>
        design.Rooms.Where(candidate => candidate.Id == room.Id
            || (IsStairway(candidate) && candidate.UpperRoomId == room.Id)).ToArray();

    // Basic room and variant schemas are checked by the caller first.
    internal static bool AreValid(DesignData design)
    {
        var rooms = design.Rooms.ToDictionary(room => room.Id, StringComparer.Ordinal);
        var linked = new HashSet<string>(StringComparer.Ordinal);
        foreach (RoomData room in design.Rooms)
        {
            if (room.Level is < 0 or > 1) return false;
            if (!IsStairway(room)) continue;
            if (room.Level != 0 || room.Width != 2 || room.Height != 4
                || room.UpperRoomId == null || room.LowerRoomId == null
                || !rooms.TryGetValue(room.UpperRoomId, out RoomData? upper)
                || !rooms.TryGetValue(room.LowerRoomId, out RoomData? lower)
                || IsStairway(upper) || IsStairway(lower) || upper.Level != 1 || lower.Level != 0
                || !linked.Add(upper.Id) || room.Optional != upper.Optional || room.Enabled != upper.Enabled
                || room.Y != upper.Y + upper.Height || room.Y + room.Height != lower.Y
                || room.X < upper.X || room.X + room.Width > upper.X + upper.Width
                || room.X < lower.X || room.X + room.Width > lower.X + lower.Width)
                return false;
            foreach (VariantData variant in design.Variants)
            {
                bool present = variant.EnabledRooms.Contains(room.Id);
                if (present != variant.EnabledRooms.Contains(upper.Id)
                    || (present && !variant.EnabledRooms.Contains(lower.Id))) return false;
            }
            if (room.Enabled && !lower.Enabled) return false;
        }
        return design.Rooms.All(room => room.Level != 1 || linked.Contains(room.Id))
            && (string.IsNullOrEmpty(design.SpouseNpc)
                || design.Rooms.All(room => room.Level == 0 && !IsStairway(room)));
    }
}
