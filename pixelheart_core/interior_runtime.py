"""Compatibility requirements for exported designs and the Interiors companion."""

INTERIORS_MOD_ID = "Pixelheart.Interiors"
INTERIORS_MIN_VERSION = "0.2.0"
# SMAPI 4.3.2 requires Stardew Valley 1.6.14 or later:
# https://github.com/Pathoschild/SMAPI/blob/develop/docs/release-notes.md#432
INTERIORS_MIN_GAME_VERSION = "1.6.14"
INTERIORS_MIN_SMAPI_VERSION = "4.3.2"


def minimum_interiors_version(specified=""):
    """Raise a validated authored minimum to the stable companion we require."""
    if not specified:
        return INTERIORS_MIN_VERSION
    release = specified.split("+", 1)[0]
    core, separator, _ = release.partition("-")

    def numeric_key(version):
        parts = version.split(".")
        parts += ["0"] * (3 - len(parts))
        # The existing project schema accepts Unicode decimal digits too.
        parts = ["".join(str(int(char)) for char in part) for part in parts]
        # Comparing normalized digits also handles long authored components
        # without Python's integer conversion limit.
        return tuple((len(part.lstrip("0") or "0"), part.lstrip("0") or "0") for part in parts)

    current = numeric_key(core)
    required = numeric_key(INTERIORS_MIN_VERSION)
    if current < required or (current == required and separator):
        return INTERIORS_MIN_VERSION
    return specified
