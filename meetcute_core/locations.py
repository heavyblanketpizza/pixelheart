"""A curated offline list of stable Stardew Valley 1.6 location names.

IDs checked against the modding reference's location-name table on 2026-09-19:
https://stardewvalleywiki.com/Modding:Location_data#Location_names

These are GameLocation names, not Maps/* asset paths or Data/Locations farm-type
keys. This is an authoring reference, not a loaded-save registry: farm layouts,
upgrades, progression and mods affect tiles, access and NPC pathfinding. Dynamic
mine floors, multiplayer building instances and festival-only locations are not
listed. Unknown saved names remain supported through the explicit custom option.
No map files, artwork or wiki descriptions are bundled.
"""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    note: str = ""


_FARM = "The farm layout varies by farm type, buildings and mods. Check the tile in the intended save."
_HOUSE = "House upgrades change the layout. Check the tile in the intended save."
_ISLAND = "Ginger Island access and routes depend on save progress. Check this stop and its connecting route in-game."
_PROGRESS = "Access depends on save progress. Check this location and its connecting route in-game."

# Human-readable labels are kept separate from the exact, case-sensitive IDs.
VANILLA_LOCATIONS = tuple(sorted((
    Location("AdventureGuild", "Adventurer's Guild", _PROGRESS),
    Location("JoshHouse", "Alex, Evelyn & George's house"),
    Location("Backwoods", "Backwoods"),
    Location("Beach", "Beach"),
    Location("Blacksmith", "Blacksmith"),
    Location("BoatTunnel", "Boat dock behind Willy's shop", _PROGRESS),
    Location("BusStop", "Bus stop"),
    Location("Tunnel", "Bus tunnel"),
    Location("Desert", "Calico Desert", _PROGRESS),
    Location("ScienceHouse", "Carpenter's shop"),
    Location("Sunroom", "Caroline's sunroom", _PROGRESS),
    Location("Club", "Casino", _PROGRESS),
    Location("Forest", "Cindersap Forest"),
    Location("CommunityCenter", "Community Center", "Access and interior change with the Community Center route. Check the intended save."),
    Location("ElliottHouse", "Elliott's cabin"),
    Location("HaleyHouse", "Emily & Haley's house"),
    Location("Farm", "Farm", _FARM),
    Location("FarmCave", "Farm cave", "The cave's contents depend on the player's choice. Check the tile in-game."),
    Location("FarmHouse", "Farmhouse", _HOUSE),
    Location("CaptainRoom", "Ginger Island · shipwreck", _ISLAND),
    Location("IslandWestCave1", "Ginger Island · crystal cave", _ISLAND),
    Location("IslandSouth", "Ginger Island · docks & resort", _ISLAND),
    Location("IslandFarmHouse", "Ginger Island · farmhouse", _ISLAND),
    Location("IslandFarmCave", "Ginger Island · Gourmand Frog's cave", _ISLAND),
    Location("IslandEast", "Ginger Island · jungle", _ISLAND),
    Location("IslandHut", "Ginger Island · Leo's hut", _ISLAND),
    Location("IslandNorthCave1", "Ginger Island · mushroom cave", _ISLAND),
    Location("IslandNorth", "Ginger Island · north", _ISLAND),
    Location("IslandSouthEastCave", "Ginger Island · Pirate Cove", _ISLAND),
    Location("IslandFieldOffice", "Ginger Island · Professor Snail's tent", _ISLAND),
    Location("QiNutRoom", "Ginger Island · Qi's Walnut Room", _ISLAND),
    Location("IslandShrine", "Ginger Island · shrine", _ISLAND),
    Location("IslandSouthEast", "Ginger Island · southeast beach", _ISLAND),
    Location("Caldera", "Ginger Island · volcano caldera", _ISLAND),
    Location("IslandWest", "Ginger Island · west & farm", _ISLAND),
    Location("Greenhouse", "Greenhouse", "Requires restoration. Its farm entrance can move; check the connecting route and tile."),
    Location("Hospital", "Harvey's clinic"),
    Location("HarveyRoom", "Harvey's room"),
    Location("SamHouse", "Jodi, Kent, Sam & Vincent's house"),
    Location("JojaMart", "JojaMart", "Availability changes with the Community Center / Joja route. Check the intended save."),
    Location("AbandonedJojaMart", "JojaMart · abandoned", "This location is tied to the Community Center route and changes after the Missing Bundle."),
    Location("LeahHouse", "Leah's cottage"),
    Location("LeoTreeHouse", "Leo's tree house", _PROGRESS),
    Location("Tent", "Linus's tent"),
    Location("AnimalShop", "Marnie's ranch"),
    Location("ManorHouse", "Mayor's manor"),
    Location("Mine", "Mines entrance", "This is the entrance, not a mine floor. Check NPC routes before adding a stop."),
    Location("Mountain", "Mountain"),
    Location("MovieTheater", "Movie Theater", _PROGRESS),
    Location("ArchaeologyHouse", "Museum & library"),
    Location("BugLand", "Mutant Bug Lair", _PROGRESS),
    Location("SandyHouse", "Oasis", _PROGRESS),
    Location("Trailer", "Pam & Penny's trailer", "The Community Upgrade replaces this home. Check the intended save and use the upgraded house when needed."),
    Location("Trailer_Big", "Pam & Penny's upgraded house", "Requires the Community Upgrade; this is a different location from Trailer."),
    Location("Town", "Pelican Town"),
    Location("SeedShop", "Pierre's general store"),
    Location("Railroad", "Railroad", _PROGRESS),
    Location("Woods", "Secret Woods", _PROGRESS),
    Location("SebastianRoom", "Sebastian's room"),
    Location("Sewer", "Sewers", _PROGRESS),
    Location("SkullCave", "Skull Cavern entrance", "This is the entrance, not a cavern floor. Desert access depends on save progress."),
    Location("BathHouse_Entry", "Spa · entrance", _PROGRESS),
    Location("BathHouse_MensLocker", "Spa · men's locker room", _PROGRESS),
    Location("BathHouse_Pool", "Spa · pool", _PROGRESS),
    Location("BathHouse_WomensLocker", "Spa · women's locker room", _PROGRESS),
    Location("Saloon", "Stardrop Saloon"),
    Location("Summit", "Summit", _PROGRESS),
    Location("FishShop", "Willy's fish shop"),
    Location("WitchHut", "Witch's hut", _PROGRESS),
    Location("WitchSwamp", "Witch's swamp", _PROGRESS),
    Location("WitchWarpCave", "Witch's swamp warp cave", _PROGRESS),
    Location("WizardHouse", "Wizard's tower"),
    Location("WizardHouseBasement", "Wizard's tower basement", _PROGRESS),
), key=lambda location: location.name.casefold()))

LOCATIONS_BY_ID = MappingProxyType({location.id: location for location in VANILLA_LOCATIONS})


def location_name(location_id: str) -> str:
    """Return a friendly label, preserving unknown IDs without guessing."""
    location = LOCATIONS_BY_ID.get(location_id)
    return location.name if location else location_id


def location_note(location_id: str) -> str:
    """Return relevant authoring context without claiming live-game validation."""
    location = LOCATIONS_BY_ID.get(location_id)
    if location is None:
        return "Use an existing location's internal name from the game or a mod. This choice does not create a map."
    return location.note or "Check the selected tile is walkable and connected to the character's other stops in-game."
