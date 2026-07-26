"""The two belligerents, and why they are not the same country twice.

Symmetric nations made every match the same match: whatever the balance said was the
best line, both sides played it, and the only variable left was the dice. Asymmetry is
what makes a *choice* out of an opening — Aurelia cannot win Korsav's war and Korsav
cannot afford Aurelia's.

Each profile carries its opening position, its magazine, and four multipliers that say
how the world and its own people treat it. The engine reads the multipliers; nothing
here is flavour only.
"""

from typing import Any, Dict


PROFILES: Dict[str, Dict[str, Any]] = {
    "west": {
        "name": "Aurelia",
        "blurb": "Northern maritime republic. Rich, exporting, and answerable to its own press.",
        # Opening position.
        "integrity": 100,
        "morale": 58,
        "military": 64,
        "standing": 78,
        "gdp": 82,          # economic output index; drives income every turn
        "budget": 70,       # treasury, in $B
        "propaganda": 20,   # a free press makes state messaging weak and expensive
        "unrest": 12,
        "defenses": {"air": 26, "naval": 14, "cyber": 30},
        # Deep in precision air and cyber, thin at sea. One warhead, like everyone.
        "arsenal": {
            "drone_swarm": 9, "cruise_missile": 7, "naval_barrage": 3,
            "cyber_strike": 7, "nuke": 1,
        },
        "traits": {
            # The world extends Aurelia credit it has not entirely earned.
            "intl_sensitivity": 0.75,
            # ...and its public withdraws support the moment the war looks ugly.
            "unrest_sensitivity": 1.18,
            # State messaging lands badly in a country that fact-checks it.
            "propaganda_reach": 0.55,
            # An export economy has the most to lose from isolation.
            "trade_exposure": 1.35,
            # Intact industry rebuilds capacity quickly.
            "industry": 1.15,
        },
        # Read by the frontend to grow the island. Style picks the landform; the seed
        # fixes the coastline so the same country looks the same every match.
        "terrain": {"seed": 20260726, "style": "delta"},
    },
    "east": {
        "name": "Korsav",
        "blurb": "Southern military state. Poorer, armed to the teeth, and difficult to embarrass.",
        "integrity": 100,
        "morale": 68,
        "military": 80,
        "standing": 58,
        "gdp": 56,
        "budget": 96,
        "propaganda": 68,   # state media, and a population with nowhere else to read
        "unrest": 8,
        "defenses": {"air": 20, "naval": 32, "cyber": 12},
        # A deep cheap magazine and a real navy. Almost no cyber arm.
        "arsenal": {
            "drone_swarm": 13, "cruise_missile": 4, "naval_barrage": 9,
            "cyber_strike": 3, "nuke": 1,
        },
        "traits": {
            # Nobody gives Korsav the benefit of any doubt.
            "intl_sensitivity": 1.30,
            "unrest_sensitivity": 0.70,
            "propaganda_reach": 1.30,
            # Autarkic and resource-fed: sanctions hurt, but less.
            "trade_exposure": 0.70,
            "industry": 0.90,
        },
        "terrain": {"seed": 77415, "style": "highland"},
    },
}
