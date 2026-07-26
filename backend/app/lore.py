"""Why these two countries want each other dead.

Two nations at war need a quarrel that is older than the war, or every match reads as
two colours bombing each other for no stated reason. This module is that quarrel, and
it is deliberately *concrete*: a drawn line, a robbed shelf, a deported population, a
metered cable, and a ferry full of dead children. Not an ideology label.

The ideological half is real but it sits on top of the material half, which is the only
way an ideological argument is ever load-bearing. Aurelia's claim is that a state is
legitimate only if its people can remove it, which makes Korsav a garrison rather than a
country. Korsav's claim is that sovereignty is not conferred by anyone's approval, and
that Aurelian rights talk is the etiquette of the party that took the shelf and then
wrote the law that made the taking lawful. Both are internally coherent. Neither is a
strawman. That is what makes them worth negotiating over.

Everything here is load-bearing, not flavour:
  · DOCTRINE in agents.py is built from ACCOUNTS, so each commander argues its own case.
  · ARTICLES are the actual clauses of the negotiation mechanic in engine.py — an
    article is settled when the two stances are compatible, and conceding one costs
    unrest at home. That cost is why talks stall.
  · The frontend shows PARTITION and ACCOUNTS in the briefing, so a viewer knows what
    the shooting is about before it starts.
"""

from typing import Any, Dict, List

# --------------------------------------------------------------------------- the history

# One archipelago, one state, sixty-one years ago. Everything since is the argument
# about how it was divided.
PARTITION: List[Dict[str, str]] = [
    {
        "when": "61 years ago",
        "what": (
            "The Meridian Union — one country across both islands — dissolves. The north "
            "holds the deepwater ports, the shipping registry and the banks; the south "
            "holds the mines, the smelters and the men who worked them."
        ),
    },
    {
        "when": "61 years ago",
        "what": (
            "A departing arbitration commission draws the maritime boundary: the Kestrel "
            "Line. It puts four fifths of the Anvil Shelf — the gas, and the ore under it "
            "— on the Aurelian side. Aurelia signs within the week. Korsav never signs."
        ),
    },
    {
        "when": "60–59 years ago",
        "what": (
            "The Anvil Removals. Four hundred and twelve thousand people cross the strait "
            "in eighteen months. Aurelia's records call it a population exchange. Korsav's "
            "call it the clearing of the northern mining towns. Not one property claim, in "
            "either direction, has ever been heard."
        ),
    },
    {
        "when": "44 years ago",
        "what": (
            "The Meridian Cable — the Union's power interconnector, and the gas line laid "
            "beside it — is left in service and in nobody's clear ownership. Korsav ships "
            "gas north through it. Aurelia meters it and sends the bill. Both have "
            "threatened to cut it; neither has."
        ),
    },
    {
        "when": "19 years ago",
        "what": (
            "An Aurelian coastguard cutter fires on the Korsavi ferry Halcyon Seven off "
            "Bellow Reef. Eighty-four dead, thirty-one of them children. An Aurelian court "
            "finds the captain acted within a reasonable apprehension of threat. Korsav has "
            "asked for an apology every year since. It has never been given."
        ),
    },
    {
        "when": "this year",
        "what": (
            "A survey finds gas under Bellow Reef — outside the shelf, and squarely on the "
            "line neither side agrees on."
        ),
    },
]

# How each capital tells the story to itself. These are not summaries of the truth;
# they are the two things being argued.
ACCOUNTS: Dict[str, Dict[str, str]] = {
    "west": {
        "creed": "the Charter of Kestrel",
        "one_line": (
            "A government its people cannot remove is not a country, and forty years of "
            "buying gas from one has not made it into one."
        ),
        "case": (
            "Aurelia's founding document holds that authority is borrowed from the governed "
            "and returned at every election. By that reading Korsav is not a state Aurelia "
            "is quarrelling with but a garrison sitting on a people, and the Korsavi are "
            "hostages rather than citizens. The Kestrel Line is settled law that Korsav "
            "simply refuses to be bound by. The Removals were an exchange both sides "
            "carried out. Halcyon Seven was a tragedy, not a crime, and a court said so in "
            "public — which is more than any Korsavi court has ever done to anyone."
        ),
        "wound": (
            "Two generations of being leaned on by a poorer, better-armed neighbour that "
            "answers to nobody, while the world tells Aurelia to be reasonable about it."
        ),
    },
    "east": {
        "creed": "the Long Ledger",
        "one_line": (
            "Sovereignty is not granted by anyone's approval. It is what you can hold, and "
            "we have held ours with nothing but the price of holding it."
        ),
        "case": (
            "Korsav keeps a ledger, not a charter: everything owed and not paid. The line "
            "was drawn by men leaving on a boat, and it handed the north the shelf that the "
            "south dug. The Removals emptied Korsavi towns and the property was never "
            "returned. Aurelian rights talk is the etiquette of the party that took the "
            "ports, the registry and the gas and then wrote the rules that made the taking "
            "lawful. Eighty-four people died on the Halcyon Seven and the answer was a "
            "verdict. Korsav does not want Aurelia's opinion of its government. It wants "
            "the ledger closed."
        ),
        "wound": (
            "Being told to be grateful for a line it never signed, by a country that has "
            "never once said the word sorry."
        ),
    },
}


def doctrine_for(side: str) -> str:
    """The historical case a commander argues from. Injected into the system prompt."""
    mine = ACCOUNTS[side]
    theirs = ACCOUNTS["east" if side == "west" else "west"]
    return (
        f"THE QUARREL. You believe: {mine['one_line']}\n{mine['case']}\n"
        f"What you cannot forgive: {mine['wound']}\n"
        f"They believe: {theirs['one_line']} Do not argue their case for them, but do not "
        "pretend it is empty either — it is why they will not simply fold."
    )


# --------------------------------------------------------------------------- the clauses

# The negotiable articles. Every one of them is a thing named above, so a settlement
# reads as this war's settlement rather than as a generic peace treaty.
#
# core:     required for any settlement at all. Without the boundary and the new field
#           there is nothing to sign — the rest are the price of signing.
# cost:     unrest the conceding public makes it pay. This is the whole reason talks
#           stall: a government whose streets are already full cannot afford to give
#           anything away, which is exactly when it most needs to.
# holder:   which side currently has possession, and therefore whose concession is the
#           one the other is actually asking for.
ARTICLES: Dict[str, Dict[str, Any]] = {
    "kestrel_line": {
        "title": "The Kestrel Line",
        "core": True,
        "holder": "west",
        "dispute": (
            "Where the maritime boundary runs. Aurelia calls the commission's line settled "
            "law; Korsav has never signed it and calls it a line drawn by men leaving on a "
            "boat."
        ),
        "concede": {
            "west": "Aurelia reopens the line to arbitration and accepts a median revision.",
            "east": "Korsav signs the Kestrel Line as drawn and drops the claim for good.",
        },
        "cost": {"west": 10, "east": 12},
    },
    "bellow_reef": {
        "title": "Bellow Reef",
        "core": True,
        "holder": None,
        "dispute": (
            "The new gas field, sitting exactly on the line neither side agrees about. "
            "Whoever gets it can pay for the war they just fought."
        ),
        "concede": {
            "west": "Aurelia drops its sole claim and accepts joint development.",
            "east": "Korsav drops its sole claim and accepts joint development.",
        },
        "cost": {"west": 8, "east": 9},
    },
    "anvil_claims": {
        "title": "The Anvil Removals",
        "core": False,
        "holder": "west",
        "dispute": (
            "Four hundred and twelve thousand people moved, and every property claim from "
            "both directions still unheard sixty years on."
        ),
        "concede": {
            "west": "Aurelia opens a claims commission and funds the compensation.",
            "east": "Korsav withdraws the claims and accepts the Removals as settled.",
        },
        "cost": {"west": 7, "east": 11},
    },
    "meridian_cable": {
        "title": "The Meridian Cable",
        "core": False,
        "holder": "west",
        "dispute": (
            "The interconnector and the gas line beside it. Korsav ships through it; "
            "Aurelia meters it and sends the bill."
        ),
        "concede": {
            "west": "Aurelia gives up the metering and puts the cable under joint operation.",
            "east": "Korsav guarantees the flow and accepts Aurelian metering permanently.",
        },
        "cost": {"west": 6, "east": 6},
    },
    "halcyon_apology": {
        "title": "Halcyon Seven",
        "core": False,
        "holder": "west",
        "dispute": (
            "Eighty-four dead off Bellow Reef, thirty-one of them children, and a verdict "
            "instead of an apology. Korsav has asked every year for nineteen years."
        ),
        "concede": {
            "west": "Aurelia admits responsibility in the chamber and names the dead.",
            "east": "Korsav withdraws the demand and lets the verdict stand.",
        },
        # Asymmetric on purpose. Saying sorry costs an Aurelian government more at home
        # than anything else on this list; dropping the demand costs a Korsavi one more.
        "cost": {"west": 14, "east": 15},
    },
}

CORE_ARTICLES = [key for key, art in ARTICLES.items() if art["core"]]
# A settlement is both core clauses plus one more. Two clauses alone is a ceasefire with
# a gas field attached; three is a peace somebody has actually paid for.
SETTLEMENT_MINIMUM = 3


def article_brief() -> Dict[str, Any]:
    """The clause list as a commander sees it when talks are open."""
    return {
        key: {
            "title": art["title"],
            "dispute": art["dispute"],
            "must_be_settled_for_peace": art["core"],
            "currently_held_by": art["holder"] or "neither",
            "what_conceding_it_means_for_you": art["concede"],
            "unrest_it_costs_you_at_home_to_concede": art["cost"],
        }
        for key, art in ARTICLES.items()
    }
