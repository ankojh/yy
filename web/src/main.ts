import { WarMap } from "./canvas";
import {
  $, clearStage, openQuarrel, renderEvent, renderIgnitionCards, renderState,
  setDwell, setReference,
} from "./ui";
import type { GameEvent, GameState, Side } from "./types";

const API_PORT = import.meta.env.VITE_API_PORT ?? "8077";
const WS_URL = `ws://${location.hostname}:${API_PORT}/ws`;

const map = new WarMap($<HTMLCanvasElement>("map"));
const selected = new Set<string>();
let state: GameState | null = null;
let socket: WebSocket | null = null;
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

    if (ev.type === "reset") {
      clearStage();
      map.clear();
      selected.clear();
      // Reference data: the three chairs, and the names of the disputed clauses. Sent
      // once and held, because every panel below needs it and none of them owns it.
      setReference(ev.payload.panel ?? {}, ev.payload.articles ?? {});
      quarrel = { partition: ev.payload.partition ?? [], accounts: ev.payload.accounts ?? {} };
      renderIgnitionCards(ev.payload.ignitions, selected);
      // Pacing lives on the server. Tooltips and bubbles hold for exactly as long as
      // the beat they belong to rather than for a hardcoded seven seconds.
      setDwell(Number(ev.payload.reveal_ms) || 0);
      $("mode").textContent = ev.payload.mock ? "mock — no API key" : "live";
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
        Number(ev.payload.shot?.stopped ?? 0)
      );
    }

    renderEvent(ev, nameOf);
  };
}

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

connect();
