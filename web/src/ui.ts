import type { Article, GameEvent, GameState, Nation, Side, Talks } from "./types";
import { WEAPONS, WEAPON_BY_ID } from "./types";

/** Deliberately small public HUD: cheap, heavy, and catastrophic force. */
const VISIBLE_WEAPONS = WEAPONS.filter((w) =>
  ["drone_swarm", "cruise_missile", "nuke"].includes(w.id)
);

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));

/** Short, mechanical explanations shared by setup controls and live readouts. */
const PARAM_HELP: Record<string, string> = {
  integrity: "Infrastructure and territory still functioning. If it reaches 0, the island loses.",
  military: "Operational capacity used by attacks and other military actions. It recovers gradually between turns.",
  intelligence: "Controls enemy visibility: coarse below 40, rounded estimates at 40–69, and exact operational state at 70+.",
  budget: "Treasury in billions of dollars. Actions and resource allocations spend it; pressure and blockades can drain it.",
  intl_pressure: "International isolation. It drains treasury and makes sanctioned attacks more expensive.",
  unrest: "Domestic opposition to the war. If it reaches 100, the government falls.",
  casualties: "Civilians and service members killed on this island. The toll influences unrest, appeals, and propaganda.",
  defenses: "Permanent interception capability. Higher values stop more incoming rounds in that domain.",
  defense_air: "Air defence intercepts drones and cruise missiles. Nuclear weapons ignore it.",
  defense_naval: "Naval defence intercepts incoming naval barrages.",
  defense_cyber: "Cyber defence reduces the effect of hostile cyber operations.",
  arsenal: "Rounds and influence operations remaining. Conventional stocks can be replenished with treasury; nuclear weapons cannot.",
  narrative: "Finite influence operations used by propaganda. False claims can backfire.",
  drone_swarm: "Cheap air-domain attack with a large salvo and relatively low diplomatic cost.",
  cruise_missile: "Heavy air-domain precision strike. Powerful, expensive, and politically costly.",
  naval_barrage: "Heavy naval-domain attack. Blockades make it harder and more expensive to launch.",
  cyber_strike: "Cheap cyber-domain attack that bypasses physical air and naval defences.",
  nuke: "A single catastrophic escalation. It cannot be intercepted or replenished.",
  council_budget: "Finite Meridian Council funds available for aid requested by either island.",
};

const info = (key: string) => {
  const copy = PARAM_HELP[key];
  if (!copy) return "";
  return '<span class="param-info" tabindex="0" role="note" aria-label="' + esc(copy)
    + '">i<span class="param-tip">' + esc(copy)
    + '</span></span>';
};

const paramLabel = (label: string, key: string) =>
  '<span class="param-label">' + esc(label) + info(key) + '</span>';

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

const WEAPON_ART: Record<string, string> = {
  narrative: '<path d="M4 6.5h16v9H9.2L5 19v-3.5H4z"/><path d="M8 10h8M8 12.5h5"/>',
  drone_swarm: '<circle cx="12" cy="12" r="2.2"/><path d="M9.8 10.5 6 7H3.5M14.2 10.5 18 7h2.5M9.8 13.5 6 17H3.5M14.2 13.5 18 17h2.5M6 7v3M18 7v3M6 14v3M18 14v3"/>',
  cruise_missile: '<path d="m4 15 11-7 5 1-3 4-10 5z"/><path d="m10 14-3-4 2-1.5 4 3M14.5 13.8l.5 4.2-2 .9-2.3-3.3"/>',
  naval_barrage: '<path d="M3 17c2 0 2-1.5 4-1.5S9 17 11 17s2-1.5 4-1.5 2 1.5 4 1.5 2-1.5 3-1.5"/><path d="M6 14h12l-1.5-5H10zM12 9V5h4v4M5 12h2"/>',
  cyber_strike: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="m8 10 2 2-2 2M12.5 14H16M12 5V3M12 21v-2M4 12H2M22 12h-2"/>',
  nuke: '<circle cx="12" cy="12" r="2.2"/><path d="M10.4 10.5 7.2 5a8 8 0 0 1 4.8-1.6v6.4M13.6 10.5 16.8 5A8 8 0 0 1 20 9.5l-5.8 2M12 14.2v6.4A8 8 0 0 1 7.2 19l3.2-5.5"/>',
};

const weaponIcon = (id: string) => `<span class="weapon-icon" aria-hidden="true">
  <svg viewBox="0 0 24 24">${WEAPON_ART[id] ?? WEAPON_ART.cruise_missile}</svg>
</span>`;

const flag = (n: Nation) => {
  return `<div class="nation-header">
    <div class="nation-identity">
      <img class="nation-flag" src="/flags/${n.side === "west" ? "aurelia" : "korsav"}.svg"
           alt="Flag of ${esc(n.name)}" />
      <div class="nation-title">
        <span class="nation-kicker">command</span>
        <h2>${esc(n.name)}</h2>
      </div>
    </div>
  </div>`;
};

/**
 * Reference data that arrives once, on reset, and never changes during a match: which
 * model is commanding which island, and what the disputed clauses are called. Held
 * module-level because every renderer below needs it and none of them owns it.
 */
let articles: Record<string, Article> = {};
export function setReference(_models: Record<string, string>, clauses: Record<string, Article>) {
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
  if (tool === "propaganda") return "narrative warfare";
  const bits = [
    args.weapon ? WEAPON_BY_ID[args.weapon]?.label ?? args.weapon : "",
    args.target ?? "",
    args.domain ?? "",
    args.resource ? String(args.resource).replace(/_/g, " ") : "",
  ].filter(Boolean);
  return bits.length ? bits.join(" → ") : tool.replace(/_/g, " ");
}

type HudNationSnapshot = {
  budget: number;
  unrest: number;
  arsenal: Record<string, number>;
};

type HudSnapshot = {
  west: HudNationSnapshot;
  east: HudNationSnapshot;
  councilBudget: number;
  dead: number;
};

let previousHud: HudSnapshot | null = null;

const magnitude = (n: number) => {
  const value = Math.abs(n);
  return Number.isInteger(value) ? count(value) : value.toFixed(1);
};

/** A transient, signed callout; its colour reflects whether the change helped. */
const deltaBadge = (
  delta: number,
  unit: "number" | "percent" | "money",
  higherIsBetter: boolean,
) => {
  if (!delta) return "";
  const direction = delta > 0 ? "+" : "−";
  const amount = unit === "money"
    ? `$${magnitude(delta)}B`
    : `${magnitude(delta)}${unit === "percent" ? "%" : ""}`;
  const helped = delta > 0 === higherIsBetter;
  const verb = delta > 0 ? "increased" : "decreased";
  return `<span class="stat-change ${helped ? "good" : "bad"}"
    aria-label="${verb} by ${esc(amount)}">${direction}${esc(amount)}</span>`;
};

const radial = (
  label: string,
  value: string,
  percent: number,
  danger = false,
  delta = 0,
  higherIsBetter = true,
) =>
  `<div class="radial ${danger ? "danger" : ""} ${delta ? "changed" : ""}"
    style="--value:${Math.max(0, Math.min(100, percent))}">
    ${deltaBadge(delta, danger ? "percent" : "money", higherIsBetter)}
    <div class="radial-copy"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>
  </div>`;

/** The whole public nation surface: two vital signs and three recognizable weapons. */
function compactPanel(n: Nation, previous?: HudNationSnapshot): string {
  const arsenal = VISIBLE_WEAPONS.map((w) => {
    const left = n.arsenal?.[w.id] ?? 0;
    const delta = previous ? left - (previous.arsenal[w.id] ?? 0) : 0;
    const cls = [
      "hud-weapon",
      left === 0 ? "spent" : "",
      w.id === "nuke" ? "nuke" : "",
      delta ? "changed" : "",
    ].join(" ");
    const label = w.id === "cruise_missile" ? "missiles" : w.id === "drone_swarm" ? "drones" : "nuclear";
    return `<div class="${cls}" title="${esc(PARAM_HELP[w.id])}">
      ${deltaBadge(delta, "number", true)}
      ${weaponIcon(w.id)}
      <span class="hud-weapon-name">${label}</span>
      <strong>${left}</strong>
    </div>`;
  }).join("");

  return `<div class="hud-vitals">
      ${flag(n)}
      <div class="radials">
        ${radial("treasury", usd(n.budget), n.budget, false, previous ? n.budget - previous.budget : 0)}
        ${radial("internal pressure", `${n.unrest}%`, n.unrest, true, previous ? n.unrest - previous.unrest : 0, false)}
      </div>
    </div>
    <div class="hud-arsenal">${arsenal}</div>`;
}

function snapshot(state: GameState): HudSnapshot {
  const nation = (n: Nation): HudNationSnapshot => ({
    budget: n.budget,
    unrest: n.unrest,
    arsenal: Object.fromEntries(VISIBLE_WEAPONS.map((w) => [w.id, n.arsenal?.[w.id] ?? 0])),
  });
  return {
    west: nation(state.west),
    east: nation(state.east),
    councilBudget: state.world.council_budget ?? 0,
    dead: (state.west.casualties ?? 0) + (state.east.casualties ?? 0),
  };
}

function restartChangeFlash(el: HTMLElement, changed: boolean) {
  el.classList.remove("changed");
  if (!changed) return;
  // The host elements persist between renders, so force the next animation to restart.
  void el.offsetWidth;
  el.classList.add("changed");
}

export function resetStatChanges() {
  previousHud = null;
  for (const id of ["council-funds", "w-toll"]) $(id).classList.remove("changed");
}

export function renderState(state: GameState) {
  const nextHud = snapshot(state);
  $("panel-west").innerHTML = compactPanel(state.west, previousHud?.west);
  $("panel-east").innerHTML = compactPanel(state.east, previousHud?.east);
  const councilDelta = previousHud ? nextHud.councilBudget - previousHud.councilBudget : 0;
  $("council-funds").innerHTML = `<span class="lbl">council funds${info("council_budget")}</span>
    <b>${usd(nextHud.councilBudget)}</b>${deltaBadge(councilDelta, "money", true)}`;
  restartChangeFlash($("council-funds"), Boolean(councilDelta));
  // The one world-level number that is not already drawn twice in the panels. Isolation
  // used to sit here as well and said nothing the two pressure bars did not.
  const dead = nextHud.dead;
  const deadDelta = previousHud ? dead - previousHud.dead : 0;
  $("w-toll").innerHTML = dead
    ? `<span class="lbl">dead${info("casualties")}</span> <b>${count(dead)}</b>
       <span class="split">${esc(state.west.name)} ${count(state.west.casualties)} ·
       ${esc(state.east.name)} ${count(state.east.casualties)}</span>
       ${deltaBadge(deadDelta, "number", false)}`
    : "";
  restartChangeFlash($("w-toll"), Boolean(deadDelta));
  previousHud = nextHud;
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

const hideTimers: Record<string, number> = {};
const pendingVerdicts: Partial<Record<Side, { reason: string; bad: boolean }>> = {};
const waitingBubbles: Partial<Record<Side, boolean>> = {};

/** Mark a spoken line that has been queued but has not begun playback yet. */
export function deferBubble(side: Side) {
  waitingBubbles[side] = true;
}

/** A side speaks. Its bubble sits over its island until the other side answers. */
export function showBubble(
  side: Side, name: string, detail: string, text: string, nuclear: boolean,
  autoHide = true,
) {
  waitingBubbles[side] = false;
  for (const s of ["west", "east"] as Side[]) if (s !== side) hideBubble(s);

  const el = $(`bubble-${side}`);
  el.className = `bubble ${side}${nuclear ? " nuke" : ""}`;
  el.innerHTML = `<div class="who"><b>${esc(name)}</b><span class="act">${esc(detail)}</span></div>
    <div class="said">${esc(text) || "<i>silence</i>"}</div>`;
  requestAnimationFrame(() => el.classList.add("show"));

  window.clearTimeout(hideTimers[side]);
  if (autoHide) hideTimers[side] = window.setTimeout(() => hideBubble(side), dwellMs);

  const pending = pendingVerdicts[side];
  if (pending) {
    delete pendingVerdicts[side];
    appendVerdict(el, pending.reason, pending.bad);
  }
}

export function hideBubble(side: Side) {
  window.clearTimeout(hideTimers[side]);
  $(`bubble-${side}`).classList.remove("show");
}

/** Audio ended or was cancelled: retire both the visible line and any queued state. */
export function finishSpeechBubble(side: Side) {
  waitingBubbles[side] = false;
  delete pendingVerdicts[side];
  hideBubble(side);
}

/** The arbiter's call appends into the bubble that is still on screen. */
export function setVerdict(side: Side, reason: string, bad: boolean) {
  if (!reason && !bad) return;
  const el = $(`bubble-${side}`);
  if (!el.classList.contains("show")) {
    if (waitingBubbles[side]) pendingVerdicts[side] = { reason, bad };
    return;
  }
  appendVerdict(el, reason, bad);
}

function appendVerdict(el: HTMLElement, reason: string, bad: boolean) {
  const line = document.createElement("div");
  line.className = `verdict ${bad ? "bad" : ""}`;
  line.textContent = bad ? `ineffective — ${reason}` : reason;
  el.appendChild(line);
}

export function ticker(text: string, alarm = false) {
  const el = $("ticker");
  el.className = `ticker show${alarm ? " alarm" : ""}`;
  el.textContent = text;
  window.clearTimeout(hideTimers.ticker);
  hideTimers.ticker = window.setTimeout(() => el.classList.remove("show"), dwellMs);
}

export function renderEvent(
  ev: GameEvent,
  nameOf: (s: Side) => string,
  options: { messageAutoHide?: boolean } = {},
) {
  const p = ev.payload;
  switch (ev.type) {
    case "ignition":
      ticker((p.grievances as string[])[0] ?? p.cards.join(", "));
      break;

    case "injection":
      ticker(p.text, true);
      break;

    case "support_request":
      ticker(p.request?.text ?? "An island has asked the Council for help.", true);
      break;

    case "support": // Older recordings used this event name.
    case "support_response":
      ticker(`${p.text}  ·  ${usd(p.remaining)} remains`, p.approved && p.kind === "arms");
      break;

    case "message":
      showBubble(
        p.side, p.name, detailOf(p.tool, p.args), p.text,
        p.args?.weapon === "nuke", options.messageAutoHide ?? true,
      );
      break;

    case "ruling":
      setVerdict(p.side, p.reason ?? "", !p.effective);
      break;

    // `deltas` is deliberately not drawn. The numbers it carries are already on screen
    // in both nation panels, and a turn's worth of them rising off the islands buried
    // the one thing over the map worth reading: what the commanders actually said.

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
  for (const s of ["west", "east"] as Side[]) {
    delete pendingVerdicts[s];
    delete waitingBubbles[s];
    window.clearTimeout(hideTimers[s]);
    $(`bubble-${s}`).classList.remove("show");
    $(`bubble-${s}`).innerHTML = "";
  }
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
 * The council policy grid.
 *
 * Card faces carry the title and nothing else. Each action has a history and two
 * incompatible readings, so the context lives in the resolution file opened on demand.
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

/** One council action, with its context and what each capital says the signature means. */
export function openDossier(id: string) {
  const card = cards.find((c) => c.id === id);
  if (!card) return;
  $("dossier-kicker").textContent = "Meridian Council · draft resolution";
  $("dossier-title").textContent = card.label;
  $("dossier-body").innerHTML = `
    <p class="lede">${esc(card.text)}</p>
    <div class="tag sep">why this is before the council</div>
    ${timeline(card.timeline)}
    <div class="tag sep">how each capital will read your decision</div>
    <div class="two-accounts">
      <div class="acct west"><div class="tag">Aurelia</div><p>${esc(card.positions.west)}</p></div>
      <div class="acct east"><div class="tag">Korsav</div><p>${esc(card.positions.east)}</p></div>
    </div>`;

  const pick = $<HTMLButtonElement>("btn-dossier-pick");
  pick.classList.remove("hidden");
  const label = () => (picked.has(id) ? "✓ in the resolution — remove" : "add to resolution");
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
 * itself part of the UI being worked on — hence living in the bottom runtime strip
 * rather than in a debug drawer somebody has to go and open.
 *
 * Shown only when a recording is loaded. A build serving live matches never sees it.
 */
export function renderReplay(block: ReplayBlock | null) {
  const bar = $("replay");
  if (!block) {
    bar.classList.add("hidden");
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

}

export { $ };
