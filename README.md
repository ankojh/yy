# yudhyantra — war machines

Two island nations that have hated each other for sixty-one years. You decide what the war
is about; two LLM agents fight it; a third LLM agent adjudicates. You never command either
side.

## Shape

- **Aurelia** (west) and **Korsav** (east), one agent each. Every move is a **tool call** —
  `strike`, `blockade`, `fortify`, `intl_appeal`, `address_public`, `propaganda`,
  `open_talks`, `table_terms`, `accept_terms`, `walk_out`, `surrender`, `hold`. Nothing is
  free text.
- **The Arbiter** sees both declared actions and the true world state, judges each for
  credibility, and returns a bounded modifier plus a news bulletin.
- **You** ignite the conflict (one or several casus belli, stackable, plus your own), then
  inject events mid-war.

The split that keeps it honest: `engine.py` owns every number. The arbiter only ever returns
a `-2..+2` modifier and an `effective` flag, both clamped server-side. A hallucinating model
costs you flavor, never simulation integrity.

Each nation sees its own exact stats but only **coarse bands** of the enemy's
(`strong` / `holding` / `strained` / `critical`). Hidden information is the game — and a
nation running a propaganda campaign reads *calmer than it is* to the enemy's analysts,
because the only window they have onto its streets is its own broadcasters. The one number
nobody can hide is the body count.

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

Each **casus belli** card is a file rather than a headline: the card face carries the title
alone, and clicking it opens the incident, the fortnight of timeline that produced it, and
what each capital says it was. They stack, and a stacked war starts much hotter.

## The two countries are not the same country twice

A symmetric war is the same war every time: whatever the balance says is best, both sides
play it, and the only variable left is the dice. Every profile field in `nations.py` is
read by the engine — none of it is flavour.

|  | Aurelia | Korsav |
|---|---|---|
| economy | rich, export-driven (output 82) | poor, near-autarkic (output 56) |
| treasury | $70B | $96B |
| arm | precision air and cyber | deep drone magazine and a real navy |
| weak at | the sea, and cyber defence is Korsav's problem not hers | cyber, in both directions |
| the world | extends her credit (×0.75 pressure) | assumes the worst (×1.30) |
| her public | free press, turns fast (×1.18 unrest) | state media, hard to move (×0.70) |
| propaganda | persuades almost nobody (×0.55) | believed at home (×1.30) |
| isolation | ruinous — she lives on trade (×1.35) | survivable (×0.70) |

So Aurelia cannot fight Korsav's war and Korsav cannot afford Aurelia's. Aurelia loses to
her own streets; Korsav loses to an empty treasury.

## Ways it ends

Integrity, morale or standing reaching **0**; public unrest reaching **100** and taking the
government with it; a signed capitulation; or — the only ending that is not a defeat for
anybody — **a settlement at the table**.

### The two pressures

Both are per-nation, and both are earned separately.

**International pressure** is what the world does to you. It rises with every attack,
scaled by *what you fired* and *what you hit* — a drone raid on an airbase is barely
noticed, a cruise missile into a housing block costs roughly three times as much, a
protected place is charged on top of that, and a warhead is in a category of its own. It
taxes your trade and therefore your income, and above 40 it bleeds standing every turn.
`intl_appeal` is the only instrument that moves it back down, and it moves it onto *them*.

**Public unrest** is what your own people do to you. It rises from damage done to your
country, from civilian deaths on either side of the strait, from deficits, and simply from
the war continuing. `address_public` settles it honestly and slowly. `propaganda` freezes
it for three turns whatever you are actually doing — the only way to keep fighting a war
your own public has turned against — at the cost of international pressure now and a
permanently worse rate of accruing it. Get caught fabricating and it backfires at home too.

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
raises the domain's standing defence permanently *and* lays hardened cover that absorbs four
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
a ceasefire — blockades lift, both armies refit at double rate, both treasuries recover, and
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

Output is an index, 0–100, capped by the infrastructure still standing under it and sliding
down to meet it when that infrastructure is bombed. Revenue is a slice of output, taxed by
isolation and cut again by blockade. Every sortie and most other tools are paid out of the
treasury: a drone swarm is $6B, a cruise missile $18B, a blockade $16B, against an opening
war chest of $70B for Aurelia and $96B for Korsav. Run out and the tools that cost money are
simply withdrawn from the legal set; stay out and your public starts paying the shortfall in
unrest.

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
REPLAY=accord ./.venv/bin/uvicorn app.main:app --port 8077
open 'http://localhost:5173/?replay=nuclear&speed=8'    # or pick it per tab
```

Either way you get a picker in the header: which recording, how fast, and **jump to a
turn**. That last one is the point of the whole thing. You are styling the negotiating
board and the talks open on turn six; a jump replays the stream up to turn six at once —
the only correct way to arrive at a turn, since the UI is a fold — behind a flag that
suppresses the transient half of it, so thirty seconds of bubbles and floaters and eleven
salvos do not fly past on the way. What lands is the map, the meters, the board and the
last line of the ticker, exactly as they would look if you had watched.

Five recordings ship in `backend/fixtures/`, chosen by what they make the UI *do* rather
than by how good a war they are — between them they reach every branch the frontend draws:

| | | |
|---|---|---|
| `accord` | 10 turns | the table works: three rounds, five clauses signed, nobody loses |
| `nuclear` | 8 turns | a warhead, two atrocities, 77,251 dead — the numbers at their largest |
| `attrition` | 12 turns | the full distance and ten different tools, ending in capitulation |
| `surrender` | 7 turns | somebody quits: the ending that arrives as a move, not as a meter |
| `nano` | 5 turns | **real model prose** — gpt-5-nano vs gpt-4.1-nano, cut short at the table |

A fixture is an ordinary match log. Anything `logs/` collects can be dropped into
`fixtures/` and replayed, and `scripts/make_fixtures.py` regenerates the four seeded ones
and prints what each still reaches. Keep `nano`: mock prose comes out of a format string,
and it is the real transcript that finds the layout bugs a canned two-clause sentence never
will — which is also why it is the one recording that cannot be regenerated.

Two things never come from the recording. The ignition deck and the quarrel are always
built from today's code, so a log recorded before a card existed still opens the dossiers
the current build knows about; and old state dumps are re-validated on load, so fields that
did not exist when the log was written arrive with the defaults a fresh match would have
had. The three chairs *are* read back off the log — who played which island is a fact about
that match, not about this build, and the panel labels say so.

The one branch no fixture reaches is a `civilian` strike: the scripted commander never
picks that target, so no seeded recording can cover the protected-place path. It is
reachable live, and it is the only thing left on this bench you still have to pay to see.

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
more. By the last turn of a long war you can read a country's integrity off how much of it
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
  sensitive to which casus belli come up — adding four cards to the deck moved it several
  matches on its own. It wants more than 40 samples to say anything firm.
- The negotiation has no partial credit. Four clauses agreed and one core clause held is
  the same as nothing agreed, which is true of real treaties and still feels abrupt.
