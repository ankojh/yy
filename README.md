# yudhyantra — war machines

Two island nations that have hated each other for sixty-one years. You chair the neutral
Meridian Council; one small Council decision breaks the deadlock and starts the war. Two
LLM agents fight it and a third adjudicates. You can answer either island's requests, but
never command a combat move or join a side.

## Shape

- **Aurelia** (west) and **Korsav** (east), one agent each. Every move is a **tool call** —
  `strike`, `blockade`, `fortify`, `intl_appeal`, `address_public`, `propaganda`,
  `open_talks`, `table_terms`, `accept_terms`, `walk_out`, `surrender`, `hold`. Nothing is
  free text.
- **The Arbiter** sees both declared actions and the true world state, judges each for
  credibility, and returns a bounded modifier plus a news bulletin.
- **You** give history one small nudge to trigger the conflict. During the war, islands
  periodically ask for relief, stabilization money, defensive systems, conventional
  weapons, or diplomatic cover. You approve or decline from a finite Council fund; an
  unsolicited grant is not possible, and recipients decide how approved aid is used.

The split that keeps it honest: `engine.py` owns every number. The arbiter only ever returns
a `-2..+2` modifier and an `effective` flag, both clamped server-side. A hallucinating model
costs you flavor, never simulation integrity.

Each nation sees its own exact stats but only **coarse bands** of the enemy's
(`strong` / `holding` / `strained` / `critical`). Hidden information is the game. Public
unrest is visible only as a mood band, while the one number nobody can hide is the body
count.

## Three chairs, three models

One model playing both nations and then judging itself is not a war; it is a model talking
to itself. The same priors read the same brief, reach for the same tool, and then rule that
reaching for it was reasonable — and whatever comes out is a property of the model, not of
the balance.

So the two commanders get different models, in the same price tier, because the experiment
has to be about the models rather than about who paid more:

| chair | default | $/1M in · out |
|---|---|---|
| Aurelia | `gpt-5-nano` | 0.05 · 0.40 |
| Korsav | `gpt-4.1-nano` | 0.10 · 0.40 |
| Arbiter | `gpt-5-nano` | 0.05 · 0.40 |

The separation that matters is between the two **commanders**. The arbiter repeating a
family is a much smaller effect and not worth paying triple for, so it takes the cheapest
seat — which does mean the referee shares a family with Aurelia's chair. `SWAP_MODELS=1`
moves that model to Korsav's chair, so a paired run scores both arrangements and whatever
the shared family is worth cancels out between them. It also stops one model being
permanently the rich republic, which is a bias of its own.

A whole twelve-turn match costs well under a cent. Which model is in which chair is shown
in the UI beside each country's name and stamped into every match log. `NATION_MODEL`
overrides both commanders for a controlled single-model run.

## The quarrel

`lore.py` is load-bearing, not flavour: the commanders argue from it, the negotiation
clauses are drawn from it, and the briefing shows it.

Both islands were one country — the **Meridian Union** — until sixty-one years ago. On its
way out, an arbitration commission drew the **Kestrel Line** through the strait and put four
fifths of the **Anvil Shelf**'s gas on the western side. Aurelia signed within the week.
Korsav never signed. In the eighteen months that followed, four hundred and twelve thousand
people crossed the water — a population exchange in Aurelia's records, the clearing of the
western mining towns in Korsav's — and not one property claim from either direction has
ever been heard. The **Meridian Cable** was left in service and in nobody's clear ownership:
Korsav ships gas west through it, Aurelia meters it and sends the bill. Nineteen years ago
an Aurelian cutter fired on the ferry **Halcyon Seven** off Bellow Reef; eighty-four dead,
thirty-one of them children, and an Aurelian court found the captain acted within a
reasonable apprehension of threat. Korsav has asked for an apology every year since. This
year a survey found gas under Bellow Reef, squarely on the line neither of them signed.

The ideological half sits on top of the material half, which is the only way an ideological
argument is ever load-bearing:

- **Aurelia — the Charter of Kestrel.** Authority is borrowed from the governed and returned
  at every election. By that reading Korsav is not a state but a garrison sitting on a
  people, and the Korsavi are hostages rather than citizens.
- **Korsav — the Long Ledger.** Sovereignty is not conferred by anyone's approval; it is
  what you can hold. Aurelian rights talk is the etiquette of the party that took the shelf,
  the ports and the registry and then wrote the rules that made the taking lawful.

Neither is a strawman, which is what makes them worth negotiating over.

Each island has an original fictional flag in `web/public/flags`: Aurelia uses an asymmetric
maritime ensign with an ivory hoist, gold meridian, compass, and twin-island stars; Korsav
uses a rust field with a gold-edged central standard carrying three provincial lozenges.
They identify the nation panels and Council rulings without borrowing a real flag template.

The opening sits behind one neutral button: **review the matter**. It opens an
emergency-session modal: choose a scenario, then rule for Aurelia, seek middle ground, or
rule for Korsav. Each scenario includes a collapsible quick read, and an optional open-text
direction can add a condition, guarantee, or finding. The interface presents the resolution
as something that may trigger a crisis, while the simulation records it as the decision that
caused this particular war.

Once fighting begins, the Council has **$72B**, shown beside the turn counter. Every few
turns the neediest island submits one priced request. Humanitarian relief calms its streets,
stabilization funds refill its treasury, defensive systems improve interception, a weapons
grant replenishes conventional rounds, and diplomatic cover lowers international pressure.
The match waits for approval or refusal. The player never chooses a recipient, provides a
nuclear weapon, receives a combat turn, or selects a target. **Gently interfere** adds a
small Council-made complication when the player wants to stir the situation.

## The two countries are not the same country twice

A symmetric war is the same war every time: whatever the balance says is best, both sides
play it, and the only variable left is the dice. Every profile field in `nations.py` is
read by the engine — none of it is flavour.

|  | Aurelia | Korsav |
|---|---|---|
| treasury | $70B | $96B |
| arm | precision air and cyber | deep drone magazine and a real navy |
| weak at | the sea, and cyber defence is Korsav's problem not hers | cyber, in both directions |
| the world | extends her credit (×0.75 pressure) | assumes the worst (×1.30) |
| her public | free press, turns fast (×1.18 unrest) | state media, hard to move (×0.70) |
| narrative warfare | 4 operations | 7 operations |

So Aurelia cannot fight Korsav's war and Korsav cannot afford Aurelia's. Aurelia loses to
her own streets; Korsav loses to an empty treasury.

## Ways it ends

Infrastructure reaching **0**; public unrest reaching **100** and taking the government
with it; a signed capitulation; or — the only ending that is not a defeat for anybody —
**a settlement at the table**.

### The two pressures

Both are per-nation, and both are earned separately.

**International pressure** is what the world does to you. It rises with every attack,
scaled by *what you fired* and *what you hit* — a drone raid on an airbase is barely
noticed, a cruise missile into a housing block costs roughly three times as much, a
protected place is charged on top of that, and a warhead is in a category of its own. It
drains treasury directly—$1B per turn for every 25 points—and sanctions raise the cost of
attacking. `intl_appeal` can move it onto the enemy, while the player can grant diplomatic
cover or issue a censure to move it directly.

**Public unrest** is what your own people do to you. It rises from damage done to your
country, civilian deaths, an empty treasury, enemy narrative warfare, and simply from
the war continuing. Every increase is scaled by the island's political nature: Aurelia's
free public moves quickly, while Korsav's controlled public is harder to shift.
`address_public` lowers unrest. A finite narrative warfare operation targets the other
island's unrest; a fabrication can backfire at home and always adds international pressure.

The two pull against each other on purpose. Silencing your own people costs you credibility
abroad, and the country that is good at one is bad at the other.

### The dead

Casualties are not a meter and you cannot lose the war by accruing them. They are the reason
the meters move, and the specific thing an appeal or a broadcast gets to point at.

Every sortie that lands kills people in proportion to what got through and where it was
aimed: a runway kills the ground crew, a housing block kills several hundred, a warhead
kills a city. Some of what you hit is a maternity hospital, a primary school or a shelter
whether you aimed at it or not — those are recorded as **atrocities**, with the place and
the toll, and they cost the attacker extra abroad. Both commanders are shown the list, and
both tools that need a grievance tell them to name one: an appeal that cites the ward and
the number lands, and one that gestures at "their aggression" is noise. Blockades kill too,
quietly, in medicine that does not arrive.

### Defence you can see

Cover used to be a rounding error — nineteen per cent off a strike at thirty points, which
is invisible next to a ±25% arbiter modifier, so nobody ever fortified because nobody could
tell whether it had worked.

Cover is now read as **interception**: rounds are stopped whole, the count is computed in
`strike_interception()`, and the renderer draws precisely that many kills. Sixty points of
air defence takes half a drone swarm out of the sky and you watch it happen. `fortify`
raises the domain's baseline defence permanently *and* lays hardened cover that absorbs four
fifths of the next salvo through it. The panels show the number as what it buys — `air ·
stops 23%` — and the map draws a tick per ten points of cover, with a dome and a live radar
sweep over a hardened domain.

The invariant: what dies on screen is what died in the arithmetic. The renderer used to roll
its own dice off the raw cover number, so seven drones could visibly sail through a wall
that the engine said had stopped four of them.

### The table

A government that is actually losing — bombed, broke, cracking or boiling — may `open_talks`.
Nobody else can; suing for peace from a winning position is a bug report, not a move.

Opening talks stops the war. **No ordnance is legal for either side** while the table is up
— enforced in the legal set, because a ceasefire a commander can break by choosing to is not
a ceasefire — blockades lift, both armies refit at double rate, and
both publics calm down. That is the buffer, and it is deliberately abusable: a losing side
can buy three turns of repairs by asking for a peace it does not mean, and the other side
has to decide whether to keep talking to someone who is reloading. `walk_out` resumes the
war, and the world charges whoever left the room.

Five clauses are on the table, all of them named above:

| clause | | |
|---|---|---|
| **The Kestrel Line** | core | where the maritime boundary runs |
| **Bellow Reef** | core | the new gas field, on the line neither signed |
| The Anvil Removals | | 412,000 moved, and every claim still unheard |
| The Meridian Cable | | who operates the interconnector, and who meters it |
| Halcyon Seven | | an apology, nineteen years late |

A clause is settled when one side demands it and the other concedes, or when both concede
(the reef going to joint development because neither could hold it). Both demanding is a
deadlock. Peace requires **both core clauses plus at least one more**.

What makes it stall is the price: **conceding a clause costs unrest at home the moment it is
said aloud**, and the expensive ones are the ones that matter — Halcyon Seven costs an
Aurelian government fourteen points of unrest to admit and a Korsavi one fifteen to drop. So
a government losing badly *and* facing a boiling public is exactly the one that cannot sign
the peace that would save it. Two rounds agreeing nothing and the talks collapse; the table
shuts for three turns; both armies have spent the ceasefire reloading.

Across 40 seeded mock matches every war reaches a table at least once, 88 rounds are spent
at one, 55 clauses are signed, and 11 wars end in an accord rather than a defeat.

### The war economy

Money is **billions of US dollars** throughout — nothing stores a scaled number, the unit
lives in `tools.usd()` and the matching helper in `ui.ts`. A line item is not one airframe
leaving one runway: it is the sortie, the munitions, the tankers and escorts, the crews and
the month of readiness behind them — a national defence line, at the scale people read
national spending.

There is no GDP meter or automatic income. Every sortie and most other tools are paid from
a finite treasury: a drone swarm is $6B, a missile $18B, and a blockade $16B, against an
opening war chest of $70B for Aurelia and $96B for Korsav. International pressure and
blockades drain it; council stabilization funding can refill it. Run out and unaffordable
tools disappear while public unrest rises.

Bankruptcy is a way to lose a war without ever losing a battle.

## Run it

```bash
# backend
cd backend
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
MOCK=1 ./.venv/bin/uvicorn app.main:app --port 8077

# frontend
cd web && npm install && npm run dev    # http://localhost:5173
```

`MOCK=1` runs scripted commanders with no API calls — the whole loop works offline, so the
UI is usable before you spend anything. For real agents:

```bash
export OPENAI_API_KEY=sk-...
./.venv/bin/uvicorn app.main:app --port 8077
```

Balance work runs in a batch:

```bash
MOCK=1 BEAT=0 REVEAL=0 TURN_PAUSE=0 ./.venv/bin/python -m scripts.simulate 40
```

## The bench

Working on the frontend used to mean fighting a war to look at it. The UI is a fold over
the event stream, which makes a match log a complete recording of a session: play the
events back in order, at the cadence the live loop would have used, and nothing downstream
can tell the difference. No model is called, no key is needed, and the same war plays
identically as many times as you want to look at it.

```bash
REPLAY=nano ./.venv/bin/uvicorn app.main:app --port 8077
open 'http://localhost:5173/?replay=nano&speed=8'    # or pick it in the header
```

Either way you get a picker in the header: which recording, how fast, and **jump to a
turn**. That last one is the point of the whole thing. You are styling the negotiating
board and the talks open on turn six; a jump replays the stream up to turn six at once —
the only correct way to arrive at a turn, since the UI is a fold — behind a flag that
suppresses the transient half of it, so thirty seconds of bubbles and floaters and eleven
salvos do not fly past on the way. What lands is the map, the meters, the board and the
last line of the ticker, exactly as they would look if you had watched.

One recording ships in `backend/fixtures/`:

| | | |
|---|---|---|
| `nano` | 12 turns | **real model prose** — gpt-5-nano vs gpt-4.1-nano, ignited on a freeze of Korsavi assets, played the full distance to capitulation |

It used to be five: four mock wars generated from fixed seeds, picked so that between them
they reached every branch the frontend draws, plus one real transcript. The bench is the
real transcript alone now. Mock prose comes out of a format string, and it is model output
that finds the layout bugs a canned two-clause sentence never will — which is also why
this is the one recording that cannot be regenerated from a seed, and why the trade went
this way rather than the other.

What that costs is coverage. `nano` reaches `strike`, `blockade`, `intl_appeal`,
`address_public`, `propaganda`, `open_talks`, `table_terms` and `hold`, with drone swarms
and cruise missiles; `test_the_recording_reaches_the_branches_it_is_kept_for` pins exactly
that, so a newer transcript swapped in that stops drawing the negotiating board fails in
the suite rather than in a screenshot. `fortify`, `accept_terms`, `walk_out` and
`surrender` are not in this war, and neither is a nuke, an atrocity or a six-figure toll.
Those are live-only now. The seeds that used to cover them are in the git history.

A fixture is an ordinary match log. Anything `logs/` collects can be dropped into
`fixtures/` and replayed, and `scripts/make_fixtures.py` copies in the one that ships —
point its `IMPORTS` at a newer log to swap the bench over, and it prints what the new one
reaches.

Two things never come from the recording. The underlying crisis deck and the quarrel are
always built from today's code, so a log recorded before an opening existed can still use
the current crisis setup; and old state dumps are re-validated on load, so fields that
did not exist when the log was written arrive with the defaults a fresh match would have
had. The three chairs *are* read back off the log — who played which island is a fact about
that match, not about this build, and the panel labels say so.

A `civilian` strike is the branch the bench has never reached, and now never will from a
fixture: the protected-place path only opens when a commander picks that target, which
the one recording that ships does not. It is reachable live, along with the rest of the
list above — the price of a bench made of one real war instead of five picked ones.

## Config

| var | default | |
|---|---|---|
| `OPENAI_API_KEY` | — | absent ⇒ mock mode |
| `WEST_MODEL` | `gpt-5-nano` | Aurelia's chair |
| `EAST_MODEL` | `gpt-4.1-nano` | Korsav's chair |
| `ARBITER_MODEL` | `gpt-5-nano` | the referee |
| `NATION_MODEL` | — | if set, overrides *both* commanders (single-model run) |
| `SWAP_MODELS` | — | `1` reverses which model commands which island |
| `MAX_TURNS` | `12` | |
| `REVEAL` | `10.0` | seconds one declared action owns the stage |
| `TURN_PAUSE` | `4.0` | seconds between turns |
| `BEAT` | `0.6` | short punctuation pauses |
| `MOCK` | — | `1` forces scripted agents even with a key |
| `REPLAY` | — | name of a recording in `fixtures/` ⇒ every connection is a replay |
| `REPLAY_SPEED` | `1` | playback rate; `?replay=` and `?speed=` on the page URL win |
| `FIXTURES` | `backend/fixtures` | where recordings are looked up |

`REVEAL` is the pacing knob that matters. One action gets ten seconds: the statement goes
up, the ordnance flies, the damage lands, and the bubble stays readable for all of it. The
server sends the number to the frontend on reset, so tooltips live exactly as long as the
beat they belong to rather than keeping a second, quietly diverging copy of it.

Port 8077 rather than 8000 because another local project already holds 8000. Override the
frontend with `VITE_API_PORT`.

## Layout

```
backend/app/
  lore.py      the quarrel — the partition, the two accounts, the negotiable clauses
  nations.py   the two profiles — opening position, magazine, and five multipliers
  tools.py     the arsenal — function schemas, prices, interception, lethality, places
  engine.py    deterministic mechanics; owns all numbers, including the table
  agents.py    two commanders + the arbiter, each with a scripted fallback
  game.py      ignition dossiers, paced turn loop, event stream
  replay.py    the bench — recordings, turn cuts, jumps, reconstructed pacing
  main.py      FastAPI websocket
backend/fixtures/   five recorded matches, committed; see "The bench"
web/src/
  terrain.ts   island generation: relief, rivers, forest, farmland, roads, cities, airbase, port
  canvas.ts    the theatre — cached terrain, site-targeted ordnance, interception, fires, smoke
  ui.ts        one render case per event type, plus the files and the negotiating board
  main.ts      websocket + dispatch
```

The UI is a fold over the event stream, so live and replay are the same code path — that is
what the bench above is built on. Adding a mechanic means a tool schema, an engine branch,
and one line in `ui.ts`.

Strikes are aimed at **places**. The engine says `civilian`; the renderer picks the
least-damaged town on that island, flies the round to it from the attacker's actual
airbase or naval yard, craters it, and leaves it burning for the rest of the war. A blunted
strike shows interceptors climbing from a real battery — as many as the engine stopped, no
more. By the last turn of a long war you can read a country's infrastructure off how much of it
is on fire.

## Not done yet

- Cooldowns are per-nation but not surfaced in the UI — an agent's unavailable tools are
  invisible to you. The same is true of what it cannot afford.
- No ELO. One match per websocket connection, and no harness yet for scoring model A
  against model B over a run of matches with the chairs swapped, which is the obvious thing
  to do with three chairs. Matches are recorded and replayable, which is half of it.
- The arbiter is injectable in principle: a commander that writes its rationale to persuade
  the *arbiter* rather than justify its action is playing a different game. Unhandled.
- Against the scripted reference policy over 40 seeded matches: Aurelia loses 17, Korsav
  loses 9, and 14 end without a loser. Aurelia still dies of her own streets more often
  than Korsav dies of an empty treasury, which is the designed asymmetry, but the split is
  sensitive to which council resolutions open the war — adding four cards to the deck moved it several
  matches on its own. It wants more than 40 samples to say anything firm.
- The negotiation has no partial credit. Four clauses agreed and one core clause held is
  the same as nothing agreed, which is true of real treaties and still feels abrupt.
