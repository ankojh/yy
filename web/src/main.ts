import { WarMap } from "./canvas";
import {
  $, clearStage, openQuarrel, renderEvent, renderReplay, renderState,
  setDwell, setReference,
} from "./ui";
import type { ReplayBlock } from "./ui";
import type { GameEvent, GameState, Side } from "./types";

const API_PORT = import.meta.env.VITE_API_PORT ?? "8077";
// The query string is forwarded verbatim, so `?replay=accord&speed=4` on the page opens
// a socket onto that recording. One server can serve a live tab and three bench tabs.
const WS_URL = `ws://${location.hostname}:${API_PORT}/ws${location.search}`;

const map = new WarMap($<HTMLCanvasElement>("map"));
let state: GameState | null = null;
let socket: WebSocket | null = null;
// The bench: null when this connection is fighting a real match, and a description of
// the loaded recording when it is playing one back.
let replay: ReplayBlock | null = null;
// True only while the server is fast-forwarding to a turn.
let scrubbing = false;
// The sixty-one years, as sent by the server. Kept so the "read the whole quarrel"
// button in the briefing has something to open without a second round trip.
let quarrel: { partition: any[]; accounts: any } = { partition: [], accounts: {} };
let crisisScenario = "line";
let crisisPosition = "middle";
let warHeld = false;

const CRISIS_LABELS: Record<string, string> = {
  line: "the Kestrel Line dispute",
  reef: "the Bellow Reef dispute",
  reckoning: "the unresolved historical claims",
};

const nameOf = (s: Side) => (state ? state[s].name : s);

function renderSupportRequest() {
  const request = state?.world.council_request;
  const host = $("support-controls");
  host.classList.toggle("hidden", !request || Boolean(replay));
  if (!request) return;
  $("support-kicker").textContent = `${state?.[request.side].name ?? request.side} is asking`;
  $("support-description").textContent = request.text;
  $("support-cost").textContent = `${request.label} · $${request.cost}B`;
  $<HTMLButtonElement>("btn-support").disabled =
    request.cost > (state?.world.council_budget ?? 0);
}

function send(command: string, extra: Record<string, unknown> = {}) {
  socket?.readyState === WebSocket.OPEN && socket.send(JSON.stringify({ command, ...extra }));
}

function setWarHeld(held: boolean) {
  warHeld = held;
  $("btn-hold").textContent = held ? "resume war" : "hold war";
}

function renderCrisisDescription() {
  const west = state?.west.name ?? "Aurelia";
  const east = state?.east.name ?? "Korsav";
  renderPositionButton("position-west", west, "aurelia");
  renderPositionButton("position-east", east, "korsav");
  $("crisis-response-description").textContent = {
    west: `The Council would accept ${west}'s interpretation of this matter.`,
    middle: "The Council would reject both claims in full and advance a negotiated compromise.",
    east: `The Council would accept ${east}'s interpretation of this matter.`,
  }[crisisPosition] ?? "";
}

function renderPositionButton(id: string, name: string, flagName: string) {
  const button = $(id);
  const image = document.createElement("img");
  image.className = "position-flag";
  image.src = `/flags/${flagName}.svg`;
  image.alt = `Flag of ${name}`;
  const label = document.createElement("span");
  label.textContent = `Rule in Favour of ${name}`;
  button.replaceChildren(image, label);
}

function renderCrisisOptions() {
  for (const button of Array.from(
    $("crisis-scenarios").querySelectorAll<HTMLElement>("[data-scenario]")
  )) {
    button.classList.toggle("on", button.dataset.scenario === crisisScenario);
  }
  for (const button of Array.from($("crisis-positions").children) as HTMLElement[]) {
    button.classList.toggle("on", button.dataset.position === crisisPosition);
  }
  renderCrisisDescription();
}

function connect() {
  socket = new WebSocket(WS_URL);

  socket.onopen = () => {
    $("mode").textContent = "connected";
  };

  socket.onclose = () => {
    $("mode").textContent = "disconnected — retrying";
    setTimeout(connect, 1500);
  };

  socket.onmessage = (msg) => {
    const ev: GameEvent = JSON.parse(msg.data);

    // A jump is the whole event stream up to some turn, replayed at once. The UI is a
    // fold, so that is the only correct way to arrive at a turn — but the transient half
    // of it (bubbles, verdicts) is thirty seconds of animation fired in one frame, and
    // none of it belongs to the state being jumped to. The durable half still runs:
    // meters, the map, the talks board, and the last line of the ticker.
    if (ev.type === "scrub") {
      scrubbing = Boolean(ev.payload.active);
      if (scrubbing) clearStage();
      return;
    }

    // Speed changed under a running bench; the server owns how long a beat lasts.
    if (ev.type === "pace") {
      setDwell(Number(ev.payload.reveal_ms) || 0);
      return;
    }

    if (ev.type === "reset") {
      clearStage();
      map.clear();
      state = null;
      // The bench, or nothing. Sent on every reset so switching recordings refreshes
      // the jump menu along with everything else.
      replay = ev.payload.replay ?? null;
      renderReplay(replay);
      // Reference data: the three chairs, and the names of the disputed clauses. Sent
      // once and held, because every panel below needs it and none of them owns it.
      setReference(ev.payload.panel ?? {}, ev.payload.articles ?? {});
      quarrel = { partition: ev.payload.partition ?? [], accounts: ev.payload.accounts ?? {} };
      crisisScenario = "line";
      crisisPosition = "middle";
      setWarHeld(false);
      $<HTMLTextAreaElement>("crisis-prompt").value = "";
      renderCrisisOptions();
      renderSupportRequest();
      // Pacing lives on the server. Tooltips and bubbles hold for exactly as long as
      // the beat they belong to rather than for a hardcoded seven seconds.
      setDwell(Number(ev.payload.reveal_ms) || 0);
      const mode = $("mode");
      mode.classList.toggle("hidden", !replay && Boolean(ev.payload.mock));
      mode.textContent = replay ? `replay · ${replay.name}` : "live";
      $("ignition").classList.remove("hidden");
      $("run-controls").classList.add("hidden");
      $("conflict-controls").classList.add("hidden");
      return;
    }

    if (ev.type === "state") {
      state = ev.payload.state as GameState;
      renderState(state);
      map.setState(state);
      renderCrisisOptions();
      renderSupportRequest();
      if (state.world.phase !== "briefing") {
        closeCrisisDialog();
        $("ignition").classList.add("hidden");
        $("conflict-controls").classList.remove("hidden");
        $("run-controls").classList.remove("hidden");
      }
      return;
    }

    // A strike is the one event with a visual: fly it across the strait. The engine
    // has already decided how many rounds the defender's batteries kill, and it is
    // passed straight through — the renderer never rolls for interception itself, so
    // what dies on screen is what died in the arithmetic.
    if (ev.type === "message" && ev.payload.tool === "strike") {
      map.fire(
        ev.payload.side as Side,
        ev.payload.args?.weapon ?? "drone_swarm",
        ev.payload.args?.target ?? "military",
        Number(ev.payload.shot?.stopped ?? 0),
        // Jumping past a salvo still has to leave its crater behind: the burn marks on
        // the map live in the renderer, not in the state, so the round has to land.
        scrubbing
      );
    }

    // Everything `renderEvent` draws for these two is transient and belongs to the
    // moment it happened, not to the turn being jumped to. The ticker lines are not,
    // and are replayed, so a jump lands with the last bulletin still on screen.
    if (scrubbing && TRANSIENT.has(ev.type)) return;

    renderEvent(ev, nameOf);
  };
}

const TRANSIENT = new Set(["message", "ruling"]);

// Opening-position sliders. Live label on drag, one configure message on release.
type Setup = Record<string, { arsenal: Record<string, number> } & Record<string, any>>;
const setup: Setup = { west: { arsenal: {} }, east: { arsenal: {} } };

function readSlider(el: HTMLInputElement) {
  const side = el.dataset.side!;
  const value = Number(el.value);
  el.parentElement?.querySelector(".val")?.replaceChildren(String(value));
  if (el.dataset.field) setup[side][el.dataset.field] = value;
  else if (el.dataset.arm) setup[side].arsenal[el.dataset.arm] = value;
}

for (const id of ["panel-west", "panel-east"]) {
  const panel = $(id);
  panel.addEventListener("input", (e) => {
    const el = e.target as HTMLInputElement;
    if (el.type === "range") readSlider(el);
  });
  panel.addEventListener("change", (e) => {
    const el = e.target as HTMLInputElement;
    if (el.type === "range") send("configure", { setup });
  });
}

/**
 * The briefing.
 *
 * Deliberately not remembered anywhere — no cookie, no localStorage, no query flag. It
 * is shown on every single load, because this thing has no obvious verbs and a returning
 * visitor is usually somebody who came back after a week and has forgotten all of them.
 * The `?` in the header brings it back on demand.
 */
const intro = $("intro");
const dossier = $("dossier");
const crisisDialog = $("crisis-dialog");
const closeIntro = () => intro.classList.add("hidden");
const closeDossier = () => dossier.classList.add("hidden");
const closeCrisisDialog = () => crisisDialog.classList.add("hidden");

$("btn-intro").onclick = closeIntro;
intro.addEventListener("click", (e) => {
  if (e.target === intro) closeIntro();
});
$("btn-help").onclick = () => intro.classList.remove("hidden");

// The files: one incident, or the whole sixty-one years. Both land in the same panel.
$("btn-quarrel").onclick = () => openQuarrel(quarrel.partition, quarrel.accounts);
$("btn-dossier-close").onclick = closeDossier;
$("btn-crisis-close").onclick = closeCrisisDialog;
dossier.addEventListener("click", (e) => {
  if (e.target === dossier) closeDossier();
});
crisisDialog.addEventListener("click", (e) => {
  if (e.target === crisisDialog) closeCrisisDialog();
});

window.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!crisisDialog.classList.contains("hidden")) {
    closeCrisisDialog();
    return;
  }
  // Innermost first: the file opens on top of the briefing, so Escape has to close it
  // first or the briefing vanishes from under a panel that is still up.
  dossier.classList.contains("hidden") ? closeIntro() : closeDossier();
});

$("btn-ignite").onclick = () => crisisDialog.classList.remove("hidden");
$("crisis-scenarios").onclick = (event) => {
  const option = (event.target as HTMLElement).closest<HTMLElement>("[data-scenario]");
  if (!option?.dataset.scenario) return;
  crisisScenario = option.dataset.scenario;
  renderCrisisOptions();
};
$("crisis-positions").onclick = (event) => {
  const option = (event.target as HTMLElement).closest<HTMLElement>("[data-position]");
  if (!option?.dataset.position) return;
  crisisPosition = option.dataset.position;
  renderCrisisOptions();
};
$("btn-crisis-start").onclick = () => {
  const west = state?.west.name ?? "Aurelia";
  const east = state?.east.name ?? "Korsav";
  const position = {
    west: `rules in favour of ${west}`,
    middle: "calls for a negotiated middle ground",
    east: `rules in favour of ${east}`,
  }[crisisPosition];
  const direction = $<HTMLTextAreaElement>("crisis-prompt").value.trim();
  const decision = `On ${CRISIS_LABELS[crisisScenario]}, the Meridian Council ${position}.`;
  send("ignite", {
    ignitions: [],
    custom: direction ? `${decision} Additional direction: ${direction}` : decision,
  });
  // WebSocket messages are ordered: finish ignition, then let the dialogue unfold.
  setWarHeld(false);
  send("run");
  closeCrisisDialog();
};
$("btn-inject").onclick = () => {
  const detail = window.prompt(
    "Add a new fact, constraint, or Council direction for both commanders:"
  )?.trim();
  if (detail) send("inject", { text: detail });
};
$("btn-hold").onclick = () => {
  setWarHeld(!warHeld);
  send(warHeld ? "pause" : "run");
};
$("btn-support").onclick = () => {
  send("support", {
    request_id: state?.world.council_request?.id,
    approved: true,
  });
  setWarHeld(false);
  send("run");
};
$("btn-support-decline").onclick = () => {
  send("support", {
    request_id: state?.world.council_request?.id,
    approved: false,
  });
  setWarHeld(false);
  send("run");
};

/**
 * The bench.
 *
 * Switching recordings and jumping to a turn are both server-side: the server owns the
 * stream, so the only honest way for the UI to arrive somewhere is to be sent the events
 * that get it there. Nothing here reaches into the renderer to fake a state.
 */
$<HTMLSelectElement>("replay-pick").onchange = (e) => {
  const name = (e.target as HTMLSelectElement).value;
  send("replay", { name, speed: replay?.speed ?? 1 });
};
$<HTMLSelectElement>("replay-seek").onchange = (e) => {
  const el = e.target as HTMLSelectElement;
  if (el.value) send("seek", { turn: Number(el.value) });
  el.value = "";
};
$<HTMLSelectElement>("replay-speed").onchange = (e) => {
  const value = Number((e.target as HTMLSelectElement).value);
  if (replay) replay.speed = value;
  send("speed", { value });
};

connect();
