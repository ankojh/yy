import { WarMap } from "./canvas";
import {
  $, clearStage, openQuarrel, renderEvent, renderIgnitionCards, renderReplay, renderState,
  setDwell, setReference,
} from "./ui";
import type { ReplayBlock } from "./ui";
import type { GameEvent, GameState, Side } from "./types";

const API_PORT = import.meta.env.VITE_API_PORT ?? "8077";
// The query string is forwarded verbatim, so `?replay=accord&speed=4` on the page opens
// a socket onto that recording. One server can serve a live tab and three bench tabs.
const WS_URL = `ws://${location.hostname}:${API_PORT}/ws${location.search}`;

const map = new WarMap($<HTMLCanvasElement>("map"));
const selected = new Set<string>();
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

const nameOf = (s: Side) => (state ? state[s].name : s);

function send(command: string, extra: Record<string, unknown> = {}) {
  socket?.readyState === WebSocket.OPEN && socket.send(JSON.stringify({ command, ...extra }));
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
    // of it (bubbles, verdicts, damage floaters) is thirty seconds of animation fired in
    // one frame, and none of it belongs to the state being jumped to. The durable half
    // still runs: meters, the map, the talks board, and the last line of the ticker.
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
      selected.clear();
      // The bench, or nothing. Sent on every reset so switching recordings refreshes
      // the jump menu along with everything else.
      replay = ev.payload.replay ?? null;
      renderReplay(replay);
      // Reference data: the three chairs, and the names of the disputed clauses. Sent
      // once and held, because every panel below needs it and none of them owns it.
      setReference(ev.payload.panel ?? {}, ev.payload.articles ?? {});
      quarrel = { partition: ev.payload.partition ?? [], accounts: ev.payload.accounts ?? {} };
      renderIgnitionCards(ev.payload.ignitions, selected);
      // Pacing lives on the server. Tooltips and bubbles hold for exactly as long as
      // the beat they belong to rather than for a hardcoded seven seconds.
      setDwell(Number(ev.payload.reveal_ms) || 0);
      $("mode").textContent = replay
        ? `replay · ${replay.name}`
        : ev.payload.mock
          ? "mock — no API key"
          : "live";
      $("ignition").classList.remove("hidden");
      $("run-controls").classList.add("hidden");
      return;
    }

    if (ev.type === "state") {
      state = ev.payload.state as GameState;
      renderState(state);
      map.setState(state);
      if (state.world.phase !== "briefing") {
        $("ignition").classList.add("hidden");
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

    // Everything `renderEvent` draws for these three is transient and belongs to the
    // moment it happened, not to the turn being jumped to. The ticker lines are not,
    // and are replayed, so a jump lands with the last bulletin still on screen.
    if (scrubbing && TRANSIENT.has(ev.type)) return;

    renderEvent(ev, nameOf);
  };
}

const TRANSIENT = new Set(["message", "ruling", "deltas"]);

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
const closeIntro = () => intro.classList.add("hidden");
const closeDossier = () => dossier.classList.add("hidden");

$("btn-intro").onclick = closeIntro;
intro.addEventListener("click", (e) => {
  if (e.target === intro) closeIntro();
});
$("btn-help").onclick = () => intro.classList.remove("hidden");

// The files: one incident, or the whole sixty-one years. Both land in the same panel.
$("btn-quarrel").onclick = () => openQuarrel(quarrel.partition, quarrel.accounts);
$("btn-dossier-close").onclick = closeDossier;
dossier.addEventListener("click", (e) => {
  if (e.target === dossier) closeDossier();
});

window.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  // Innermost first: the file opens on top of the briefing, so Escape has to close it
  // first or the briefing vanishes from under a panel that is still up.
  dossier.classList.contains("hidden") ? closeIntro() : closeDossier();
});

$("btn-ignite").onclick = () =>
  send("ignite", { ignitions: [...selected], custom: $<HTMLInputElement>("custom").value });
$("btn-step").onclick = () => send("step");
$("btn-run").onclick = () => send("run");
$("btn-pause").onclick = () => send("pause");
$("btn-reset").onclick = () => send("reset");
$("btn-inject").onclick = () => {
  const input = $<HTMLInputElement>("inject");
  send("inject", { text: input.value });
  input.value = "";
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

/* -------------------------------------------------------------------------------
 * TEMPORARY: one-click mock run. Delete this block and the button in index.html.
 *
 * Three commands in the order the server processes them — load a recording, light it,
 * play it — so a war starts with no picking and no config, from whatever state the
 * page is in. `attrition` is the fullest one on file: twelve turns, ten different
 * tools, talks that stall twice, ending in capitulation. Change the two constants and
 * nothing else.
 *
 * Gated on the catalogue rather than shown unconditionally, because a `replay` command
 * naming a recording the server does not have falls back to a *live* match — and a
 * button whose whole promise is "this costs nothing" must not be one missing file away
 * from spending money.
 */
const MOCK_RUN = "attrition";
const MOCK_SPEED = 2;   // 1 is the real cadence; 2 halves a twelve-turn war to ~2 min

fetch(`http://${location.hostname}:${API_PORT}/replays`)
  .then((r) => r.json())
  .then((data: { replays: Array<{ name: string }> }) => {
    const have = (data.replays ?? []).map((r) => r.name);
    if (!have.length) return;
    const name = have.includes(MOCK_RUN) ? MOCK_RUN : have[0];
    const button = $("btn-mock");
    button.classList.remove("hidden");
    button.onclick = () => {
      closeIntro();
      send("replay", { name, speed: MOCK_SPEED });
      send("ignite", {});
      send("run");
    };
  })
  .catch(() => {});   // no bench, no button

connect();
