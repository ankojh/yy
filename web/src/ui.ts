import type { Article, GameEvent, GameState, Nation, Side, Talks } from "./types";
import { WEAPONS, WEAPON_BY_ID } from "./types";

/** The four meters you lose the war by emptying. */
const METERS: Array<[keyof Nation, string]> = [
  ["integrity", "integrity"],
  ["morale", "morale"],
  ["military", "military"],
  ["standing", "standing"],
];

/**
 * The two constituencies that can end a war without anybody firing anything, plus the
 * instrument that plays them off against each other. These fill *upwards* into danger,
 * which is why they are drawn in their own colours rather than as more green bars.
 */
const PRESSURES: Array<[keyof Nation, string, string]> = [
  ["intl_pressure", "international pressure", "intl"],
  ["unrest", "public unrest", "unrest"],
  ["propaganda", "state propaganda", "spin"],
];

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));

/** Bodies get separators. "1240 dead" and "1,240 dead" are not the same sentence. */
const count = (n: number) => n.toLocaleString("en-US");

/**
 * Money.
 *
 * Every price and treasury figure the server sends is in billions of US dollars — the
 * scale lives here and in `tools.usd()`, and nothing on either side stores a scaled
 * number. Written out at the point of display so a $6B drone sortie sits next to a $70B
 * treasury and the arithmetic stays legible.
 */
const usd = (n: number) => `$${Math.round(n).toLocaleString("en-US")}B`;

/**
 * Reference data that arrives once, on reset, and never changes during a match: which
 * model is commanding which island, and what the disputed clauses are called. Held
 * module-level because every renderer below needs it and none of them owns it.
 */
let panel: Record<string, string> = {};
let articles: Record<string, Article> = {};
export function setReference(models: Record<string, string>, clauses: Record<string, Article>) {
  panel = models ?? {};
  articles = clauses ?? {};
}
const clauseTitle = (id: string) => articles[id]?.title ?? id;

/**
 * How long a bubble or a ticker line stays readable.
 *
 * The server owns pacing — it holds each declared action on stage for REVEAL seconds —
 * so the UI asks it rather than keeping a second, quietly diverging copy of the number.
 */
let dwellMs = 11000;
export function setDwell(revealMs: number) {
  if (revealMs > 0) dwellMs = revealMs + 1200;
}

/** "cruise missile → military", or "" for moves with no target. */
function detailOf(tool: string, args: Record<string, any> = {}): string {
  const bits = [
    args.weapon ? WEAPON_BY_ID[args.weapon]?.label ?? args.weapon : "",
    args.target ?? "",
    args.domain ?? "",
  ].filter(Boolean);
  return bits.length ? bits.join(" → ") : tool.replace(/_/g, " ");
}

const slider = (
  side: Side, label: string, attr: string, key: string, value: number, max: number
) => `<div class="meter edit">
    <label><span>${label}</span><span class="val">${value}</span></label>
    <input type="range" min="0" max="${max}" value="${value}"
           data-side="${side}" data-${attr}="${key}" />
  </div>`;

/** Before the war the panel is a control surface; after it, a readout. */
function setupPanel(n: Nation): string {
  const stats = METERS.map(([key, label]) =>
    slider(n.side, label, "field", key as string, n[key] as number, 100)
  ).join("");

  // The economy and the home front are opening positions too — a rich nervous republic
  // and a poor obedient one are the same sliders set differently.
  const country = [
    slider(n.side, "output (GDP)", "field", "gdp", n.gdp, 100),
    slider(n.side, "treasury ($B)", "field", "budget", n.budget, 300),
    slider(n.side, "state propaganda", "field", "propaganda", n.propaganda, 100),
    slider(n.side, "public unrest", "field", "unrest", n.unrest, 100),
  ].join("");

  const arms = WEAPONS.map((w) =>
    slider(n.side, w.label, "arm", w.id, n.arsenal?.[w.id] ?? 0, w.id === "nuke" ? 5 : 30)
  ).join("");

  return `<h2>${n.name}</h2>
    ${commander(n)}
    ${n.blurb ? `<p class="blurb">${esc(n.blurb)}</p>` : ""}
    ${n.creed ? `<p class="creed">argues from ${esc(n.creed)}</p>` : ""}
    <div class="block"><div class="tag">opening position</div>${stats}</div>
    <div class="block"><div class="tag">country</div>${country}</div>
    <div class="block"><div class="tag">arsenal</div>${arms}</div>`;
}

function readoutPanel(n: Nation): string {
  const meters = METERS.map(([key, label]) => {
    const v = n[key] as number;
    return `<div class="meter">
      <label><span>${label}</span><span>${v}</span></label>
      <div class="track"><div class="fill ${v < 30 ? "low" : ""}" style="width:${v}%"></div></div>
    </div>`;
  }).join("");

  // Pressure bars run the other way: full is fatal, so they are never green.
  const pressures = PRESSURES.map(([key, label, cls]) => {
    const v = n[key] as number;
    const hot = cls !== "spin" && v >= 70 ? " hot" : "";
    return `<div class="meter">
      <label><span>${label}</span><span>${v}</span></label>
      <div class="track"><div class="fill ${cls}${hot}" style="width:${v}%"></div></div>
    </div>`;
  }).join("");

  // The ledger. Output is capped by the infrastructure still standing under it, which
  // is why the ceiling is worth showing next to the number.
  const ceiling = Math.min(n.gdp_base ?? 100, n.integrity);
  const economy = `<div class="d"><span>output</span><span>${n.gdp} / ${ceiling}</span></div>
    <div class="d"><span>treasury</span><span class="pct">${usd(n.budget)}</span></div>`;

  // Defences are shown as what they actually do — the share of an incoming salvo that
  // gets shot down — rather than as a bare number nobody can price. The percentage is
  // the engine's own INTERCEPT_DIVISOR and CEILING; a hardened domain gets the dome.
  const defenses = Object.entries(n.defenses)
    .map(([d, v]) => {
      const turns = n.effects?.shield?.[d] ?? 0;
      const stops = Math.round(Math.min(0.72, v / 105) * 100);
      const cover = turns > 0 ? `<span class="shield">◈ hardened ${turns}t</span>` : "";
      return `<div class="d ${turns > 0 ? "hard" : ""}">
        <span>${d}</span>${cover}<span class="pct">stops ${stops}%</span></div>`;
    })
    .join("");

  // Everything currently acting on this nation without anyone spending a turn on it.
  const fx = n.effects ?? {
    shield: {}, morale_buffer: 0, blockaded: 0, sanctioned: 0, spin: 0, deficit_turns: 0,
  };
  const status = [
    fx.blockaded > 0 ? `<span class="chip bad">blockaded ${fx.blockaded}t</span>` : "",
    fx.sanctioned > 0 ? `<span class="chip bad">sanctioned ${fx.sanctioned}t</span>` : "",
    fx.deficit_turns > 0 ? `<span class="chip bad">bankrupt ${fx.deficit_turns}t</span>` : "",
    fx.spin > 0 ? `<span class="chip spin">broadcasting ${fx.spin}t</span>` : "",
    fx.morale_buffer > 0 ? `<span class="chip good">resolve +${fx.morale_buffer}</span>` : "",
    n.strike_streak >= 2
      ? `<span class="chip bad">forces spent</span>`
      : n.strike_streak === 1
        ? `<span class="chip warn">fatigued</span>`
        : "",
  ].filter(Boolean).join("");

  // Rounds remaining. Spent racks stay visible but dim — running dry is information.
  const arsenal = WEAPONS.map((w) => {
    const left = n.arsenal?.[w.id] ?? 0;
    const cls = ["w", left === 0 ? "spent" : "", w.id === "nuke" ? "nuke" : ""].join(" ");
    const pips = w.id === "nuke" ? "☢".repeat(left) : "▮".repeat(Math.min(left, 10));
    return `<div class="${cls}"><span>${w.label}</span><span class="pips">${pips}</span><span class="n">${left}</span></div>`;
  }).join("");

  return `<h2>${n.name}</h2>
    ${commander(n)}
    ${status ? `<div class="chips">${status}</div>` : ""}
    ${meters}
    <div class="block"><div class="tag">pressure</div>${pressures}</div>
    <div class="block"><div class="tag">the dead</div>
      <div class="d toll-line"><span>civilians and service dead</span>
        <span class="n">${count(n.casualties ?? 0)}</span></div>
    </div>
    <div class="block"><div class="tag">war economy</div>${economy}</div>
    <div class="block"><div class="tag">air, sea and network defence</div>${defenses}</div>
    <div class="block"><div class="tag">arsenal</div>${arsenal}</div>`;
}

/**
 * Which model is sitting in this chair, on its own line under the country's name.
 *
 * Shown because a match between two different models is only worth anything if you can
 * see which was which. It used to hang off the end of a "western island" label, which
 * put a compass bearing on screen next to two islands drawn left and right and a lore
 * that calls them something else again — so the label is gone and the model stands on
 * its own. Nothing is drawn when the scripted cabinets are playing: there is no chair to
 * name, and an empty line would only push the country's description down.
 */
function commander(n: Nation): string {
  const model = panel[n.side];
  return model && model !== "mock" ? `<div class="chair">${esc(model)}</div>` : "";
}

/** Sliders must survive their own state echo — re-rendering mid-drag would fight the user. */
let setupShown = false;

export function renderState(state: GameState) {
  const briefing = state.world.phase === "briefing";
  if (!briefing || !setupShown) {
    const build = briefing ? setupPanel : readoutPanel;
    $("panel-west").innerHTML = build(state.west);
    $("panel-east").innerHTML = build(state.east);
    setupShown = briefing;
  }
  $("w-tension").style.width = `${state.world.tension}%`;
  // The one world-level number that is not already drawn twice in the panels. Isolation
  // used to sit here as well and said nothing the two pressure bars did not.
  const dead = (state.west.casualties ?? 0) + (state.east.casualties ?? 0);
  $("w-toll").innerHTML = dead
    ? `<span class="lbl">dead</span> <b>${count(dead)}</b>
       <span class="split">${esc(state.west.name)} ${count(state.west.casualties)} ·
       ${esc(state.east.name)} ${count(state.east.casualties)}</span>`
    : "";
  renderTalks(state);
  $("turn").textContent = `turn ${state.world.turn}`;
  $("phase").textContent = state.world.talks?.open ? "ceasefire" : state.world.phase;

  const outcome = $("outcome");
  if (state.world.outcome) {
    outcome.textContent = state.world.outcome;
    outcome.classList.remove("hidden");
  } else {
    outcome.classList.add("hidden");
  }
}

/**
 * The table, drawn on the water between the two islands.
 *
 * Four clauses with two stances each is more state than a ticker line can carry, and
 * the whole drama of a negotiation is watching one column move while the other does
 * not. So it gets a board: who is holding out, who has folded, and how many rounds are
 * left before the guns come back on.
 */
const STANCE: Record<string, string> = {
  demand: "holds out",
  concede: "concedes",
  silent: "—",
};

function renderTalks(state: GameState) {
  const el = $("talks");
  const talks: Talks | undefined = state.world.talks;
  if (!talks?.open) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");

  const ids = Object.keys(articles).length ? Object.keys(articles) : Object.keys(talks.positions);
  const rows = ids
    .map((id) => {
      const done = talks.settled.includes(id);
      const west = talks.positions[id]?.west ?? "silent";
      const east = talks.positions[id]?.east ?? "silent";
      return `<div class="clause ${done ? "signed" : ""}">
        <span class="w ${west}">${STANCE[west] ?? west}</span>
        <span class="t">${esc(clauseTitle(id))}${articles[id]?.core ? " <i>core</i>" : ""}</span>
        <span class="e ${east}">${STANCE[east] ?? east}</span>
      </div>`;
    })
    .join("");

  const opener = talks.opened_by ? state[talks.opened_by].name : "";
  const warn = talks.deadlock
    ? `<span class="warn">${talks.deadlock} round${talks.deadlock > 1 ? "s" : ""} with nothing agreed</span>`
    : "<span>guns silent, both sides refitting</span>";

  el.innerHTML = `<div class="talks-head">
      <span class="tag">ceasefire · round ${talks.round + 1} · opened by ${esc(opener)}</span>
      ${warn}
    </div>${rows}`;
}

const ISLAND_X: Record<Side, string> = { west: "23.5%", east: "76.5%" };
const hideTimers: Record<string, number> = {};

/** A side speaks. Its bubble sits over its island until the other side answers. */
export function showBubble(
  side: Side, name: string, detail: string, text: string, nuclear: boolean
) {
  for (const s of ["west", "east"] as Side[]) if (s !== side) hideBubble(s);

  const el = $(`bubble-${side}`);
  el.className = `bubble ${side}${nuclear ? " nuke" : ""}`;
  el.innerHTML = `<div class="who"><b>${esc(name)}</b><span class="act">${esc(detail)}</span></div>
    <div class="said">${esc(text) || "<i>silence</i>"}</div>`;
  requestAnimationFrame(() => el.classList.add("show"));

  window.clearTimeout(hideTimers[side]);
  hideTimers[side] = window.setTimeout(() => hideBubble(side), dwellMs);
}

function hideBubble(side: Side) {
  $(`bubble-${side}`).classList.remove("show");
}

/** The arbiter's call appends into the bubble that is still on screen. */
export function setVerdict(side: Side, reason: string, bad: boolean) {
  const el = $(`bubble-${side}`);
  if (!el.classList.contains("show") || (!reason && !bad)) return;
  const line = document.createElement("div");
  line.className = `verdict ${bad ? "bad" : ""}`;
  line.textContent = bad ? `ineffective — ${reason}` : reason;
  el.appendChild(line);
}

/** What a delta is called on screen, and which direction of it is bad news. */
const FIELD_LABEL: Record<string, string> = {
  intl_pressure: "isolation",
  unrest: "unrest",
  gdp: "output",
  budget: "treasury",
  propaganda: "propaganda",
  casualties: "dead",
};
const RISING_IS_BAD = new Set(["intl_pressure", "unrest", "casualties"]);

/**
 * Damage rises off whichever island actually took it.
 *
 * A turn can now move seven meters at once, so the vertical offset is stepped by index
 * rather than jittered — at random the lines landed on top of each other and on the
 * speech bubble, and a turn's consequences were unreadable exactly when they mattered.
 */
export function floater(side: Side, text: string, good: boolean, index = 0, grave = false) {
  const host = $("floaters");
  const el = document.createElement("div");
  el.className = `floater ${good ? "up" : "down"}${grave ? " dead" : ""}`;
  el.textContent = text;
  el.style.left = ISLAND_X[side];
  el.style.top = `${58 + (index % 6) * 5.5}%`;
  el.style.animationDelay = `${index * 90}ms`;
  host.appendChild(el);
  window.setTimeout(() => el.remove(), 2900 + index * 90);
}

export function ticker(text: string, alarm = false) {
  const el = $("ticker");
  el.className = `ticker show${alarm ? " alarm" : ""}`;
  el.textContent = text;
  window.clearTimeout(hideTimers.ticker);
  hideTimers.ticker = window.setTimeout(() => el.classList.remove("show"), dwellMs);
}

export function renderEvent(ev: GameEvent, nameOf: (s: Side) => string) {
  const p = ev.payload;
  switch (ev.type) {
    case "ignition":
      ticker((p.grievances as string[])[0] ?? p.cards.join(", "));
      break;

    case "injection":
      ticker(p.text, true);
      break;

    case "message":
      showBubble(p.side, p.name, detailOf(p.tool, p.args), p.text, p.args?.weapon === "nuke");
      break;

    case "ruling":
      setVerdict(p.side, p.reason ?? "", !p.effective);
      break;

    case "deltas": {
      // Both capitals can take damage from one action, and each side's lines have to
      // stack independently or they interleave into nonsense.
      const seen: Record<string, number> = { west: 0, east: 0 };
      for (const d of p.deltas as any[]) {
        const label = FIELD_LABEL[d.field] ?? d.field;
        const rising = d.delta > 0;
        const good = RISING_IS_BAD.has(d.field) ? !rising : rising;
        // Two deltas are not meter ticks and must not be read as one. The dead are a
        // count of people; the treasury is money, and a bare "-18" next to a row of bars
        // that top out at 100 invites you to read a sortie as eighteen per cent of
        // something.
        const text =
          d.field === "casualties"
            ? `${nameOf(d.side)} — ${count(Math.abs(d.delta))} dead`
            : d.field === "budget"
              ? `${nameOf(d.side)} ${label} ${rising ? "+" : "−"}${usd(Math.abs(d.delta))}`
              : `${nameOf(d.side)} ${label} ${rising ? "+" : ""}${d.delta}`;
        floater(d.side, text, good, seen[d.side]++, d.field === "casualties");
      }
      break;
    }

    case "note":
      ticker(p.text, true);
      break;

    case "bulletin":
      ticker(
        p.condemned ? `${p.text}  —  ${nameOf(p.condemned)} is condemned` : p.text,
        Boolean(p.condemned)
      );
      break;
  }
}

export function clearStage() {
  setupShown = false;
  for (const s of ["west", "east"] as Side[]) {
    $(`bubble-${s}`).classList.remove("show");
    $(`bubble-${s}`).innerHTML = "";
  }
  $("floaters").innerHTML = "";
  $("ticker").classList.remove("show");
  $("talks").classList.add("hidden");
  $("w-toll").innerHTML = "";
}

/* ------------------------------------------------------------------ the files */

export interface Ignition {
  id: string;
  label: string;
  text: string;
  timeline: Array<{ when: string; what: string }>;
  positions: Record<Side, string>;
}

let cards: Ignition[] = [];
let picked: Set<string> = new Set();

/**
 * The ignition grid.
 *
 * Card faces carry the title and nothing else. A paragraph of context clamped to four
 * lines in a 150px box is a paragraph nobody reads, and each of these incidents has a
 * fortnight behind it that explains the war that follows — so the context moves into a
 * file you open, and the grid goes back to being a list of choices.
 */
export function renderIgnitionCards(list: Ignition[], selected: Set<string>) {
  cards = list;
  picked = selected;
  const host = $("cards");
  host.innerHTML = "";
  for (const card of list) {
    const el = document.createElement("button");
    el.className = "card";
    el.dataset.id = card.id;
    el.innerHTML = `<span class="t">${esc(card.label)}</span><span class="chev">file →</span>`;
    el.onclick = () => openDossier(card.id);
    host.appendChild(el);
  }
  syncCards();
}

function syncCards() {
  for (const el of Array.from($("cards").children) as HTMLElement[]) {
    el.classList.toggle("on", picked.has(el.dataset.id ?? ""));
  }
}

function toggle(id: string) {
  picked.has(id) ? picked.delete(id) : picked.add(id);
  syncCards();
}

const timeline = (rows: Array<{ when: string; what: string }>) =>
  `<ol class="timeline">${rows
    .map((r) => `<li><span class="when">${esc(r.when)}</span><span>${esc(r.what)}</span></li>`)
    .join("")}</ol>`;

/** One incident, with the fortnight that produced it and what each capital says it was. */
export function openDossier(id: string) {
  const card = cards.find((c) => c.id === id);
  if (!card) return;
  $("dossier-kicker").textContent = "casus belli · file";
  $("dossier-title").textContent = card.label;
  $("dossier-body").innerHTML = `
    <p class="lede">${esc(card.text)}</p>
    <div class="tag sep">how it got here</div>
    ${timeline(card.timeline)}
    <div class="tag sep">what each capital says it was</div>
    <div class="two-accounts">
      <div class="acct west"><div class="tag">Aurelia</div><p>${esc(card.positions.west)}</p></div>
      <div class="acct east"><div class="tag">Korsav</div><p>${esc(card.positions.east)}</p></div>
    </div>`;

  const pick = $<HTMLButtonElement>("btn-dossier-pick");
  pick.classList.remove("hidden");
  const label = () => (picked.has(id) ? "✓ on the table — remove" : "use this casus belli");
  pick.textContent = label();
  pick.onclick = () => {
    toggle(id);
    pick.textContent = label();
  };
  $("dossier").classList.remove("hidden");
}

/** The sixty-one years, and the clauses that are still open because of them. */
export function openQuarrel(
  partition: Array<{ when: string; what: string }>,
  accounts: Record<string, { creed: string; one_line: string; case: string; wound: string }>
) {
  $("dossier-kicker").textContent = "the quarrel · sixty-one years";
  $("dossier-title").textContent = "The Meridian Partition";
  $("dossier-body").innerHTML = `
    ${timeline(partition)}
    <div class="tag sep">the two accounts</div>
    <div class="two-accounts">
      ${(["west", "east"] as Side[])
        .map(
          (s) => `<div class="acct ${s}">
            <div class="tag">${s === "west" ? "Aurelia" : "Korsav"} — ${esc(accounts[s].creed)}</div>
            <p class="one-line">${esc(accounts[s].one_line)}</p>
            <p>${esc(accounts[s].case)}</p>
            <p class="wound">Cannot forgive: ${esc(accounts[s].wound)}</p>
          </div>`
        )
        .join("")}
    </div>
    <div class="tag sep">still open, and negotiable</div>
    <div class="clauses">${Object.entries(articles)
      .map(
        ([, a]) => `<div class="clause-row">
          <b>${esc(a.title)}${a.core ? " <i>core</i>" : ""}</b>
          <span>${esc(a.dispute)}</span></div>`
      )
      .join("")}</div>`;
  $("btn-dossier-pick").classList.add("hidden");
  $("dossier").classList.remove("hidden");
}

/* ------------------------------------------------------------------ the bench */

/** One recorded match, as the server describes it. */
export interface Recording {
  name: string;
  title: string;
  turns: number;
  events: number;
  outcome: string;
  complete: boolean;
  live: boolean;
  dead: number;
  marks: Array<{ turn: number; label: string }>;
}

export interface ReplayBlock {
  name: string;
  speed: number;
  current: Recording;
  available: Recording[];
}

const option = (value: string, text: string) =>
  `<option value="${esc(value)}">${esc(text)}</option>`;

/**
 * The replay bar.
 *
 * Three controls and no more: which war, which turn, how fast. It exists so the UI can
 * be worked on against a real match without a model being called, which means it is
 * itself part of the UI being worked on — hence living in the header next to the mode
 * pill rather than in a debug drawer somebody has to go and open.
 *
 * Shown only when a recording is loaded. A build serving live matches never sees it.
 */
export function renderReplay(block: ReplayBlock | null) {
  const bar = $("replay");
  const note = $("replay-note");
  if (!block) {
    bar.classList.add("hidden");
    note.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");

  $<HTMLSelectElement>("replay-pick").innerHTML =
    block.available
      .map((r) => option(r.name, `${r.name} — ${r.turns} turns · ${r.title}`))
      .join("") +
    // The way out. Named for what it costs, because it is the one control here that
    // does cost something.
    option("", "— stop replaying · fight a live match (spends API credit) —");
  $<HTMLSelectElement>("replay-pick").value = block.name;

  // Every turn is jumpable, but the ones worth naming get named: a turn where somebody
  // sued for terms is the turn you want when you are styling the table.
  $<HTMLSelectElement>("replay-seek").innerHTML =
    option("", "jump to…") + block.current.marks.map((m) => option(String(m.turn), m.label)).join("");
  $<HTMLSelectElement>("replay-seek").value = "";
  $<HTMLSelectElement>("replay-speed").value = String(block.speed);

  const ending = block.current.complete
    ? esc(block.current.outcome)
    : "this recording stops mid-war — it was never played to a verdict";
  note.classList.remove("hidden");
  note.innerHTML = `<b>replaying “${esc(block.current.name)}”</b> — ${esc(
    block.current.title
  )}. ${block.current.turns} turns, ${count(block.current.dead)} dead. The deck below is
    live for reading the files, but the war is already chosen: <em>ignite</em> plays this
    one back. <span class="dim">${ending}</span>`;
}

export { $ };
