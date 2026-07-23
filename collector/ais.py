"""AIS ship-type code -> coarse vessel_class.

Reality check the brief already flags: AIS ship-type is coarse. Codes 70-79
are all "Cargo" with NO on-wire distinction between bulk carrier, container,
and general cargo. Gas carriers ride in the tanker band (80-89). So we emit a
COARSE class here and lean on the berth tag for commodity attribution — the
finer vessel_class_hint is only a fallback at multi-commodity berths.

NavigationalStatus codes we care about: 1 = at anchor, 5 = moored.
"""
from __future__ import annotations

NAV_AT_ANCHOR = 1
NAV_MOORED = 5
STATIONARY_NAV = {NAV_AT_ANCHOR, NAV_MOORED}


def ais_type_to_class(ais_type: int | None) -> str:
    if ais_type is None:
        return "unknown"
    t = int(ais_type)
    if 70 <= t <= 79:
        return "cargo"          # bulk / container / general — indistinguishable via AIS
    if 80 <= t <= 89:
        return "tanker"         # includes LNG/LPG/ammonia gas carriers
    if t == 30:
        return "fishing"
    if 31 <= t <= 32:
        return "tug"            # towing
    if 33 <= t <= 35:
        return "special"        # dredging / diving / military ops
    if 36 <= t <= 37:
        return "pleasure"       # sailing / pleasure craft
    if 40 <= t <= 49:
        return "hsc"            # high-speed craft
    if t in (50, 52, 53):
        return "tug"            # pilot(50)/tug(52)/port tender(53)
    if 50 <= t <= 59:
        return "special"
    if 60 <= t <= 69:
        return "passenger"
    return "other"


def is_stationary(sog: float | None, nav_status: int | None, sog_threshold: float) -> bool:
    """A vessel counts as 'stopped/slow' if it's very slow OR AIS says it's
    anchored/moored (covers cases where SOG is briefly noisy)."""
    if nav_status in STATIONARY_NAV:
        return True
    if sog is None:
        return False
    return sog < sog_threshold
