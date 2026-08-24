import type { GameEvent, Side } from "./types";

type DevAgent = Side | "arbiter";
type Sender = (command: string, extra?: Record<string, unknown>) => void;

interface DevTrace {
  agent: DevAgent;
  direction: "sent" | "received" | "usage" | "error" | "status" | string;
  model?: string;
  provider?: string;
  api?: string;
  turn?: number;
  timestamp?: string;
  elapsed_ms?: number | null;
  content?: unknown;
  [key: string]: unknown;
}

const agents: DevAgent[] = ["west", "east", "arbiter"];
const traces: Record<DevAgent, DevTrace[]> = { west: [], east: [], arbiter: [] };
let sender: Sender | null = null;
let available = false;
let enabled = false;
let active: DevAgent = "west";
let provider = "";
let panel: Record<string, string> = {};

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

function label(agent: DevAgent): string {
  return agent === "west" ? "Aurelia" : agent === "east" ? "Korsav" : "Arbiter";
}

function format(value: unknown): string {
  if (typeof value === "string") {
    const trimmed = value.trim();
    if ((trimmed.startsWith("{") && trimmed.endsWith("}")) ||
        (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        return JSON.stringify(JSON.parse(trimmed), null, 2);
      } catch {
        // It only looked like JSON. Preserve the exact model text.
      }
    }
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value ?? "");
  }
}

function detailPayload(trace: DevTrace): unknown {
  if (trace.content !== undefined) return trace.content;
  const omitted = new Set([
    "agent", "direction", "model", "provider", "api", "turn", "timestamp",
    "elapsed_ms", "sequence",
  ]);
  return Object.fromEntries(Object.entries(trace).filter(([key]) => !omitted.has(key)));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function appendBlock(host: HTMLElement, title: string, value: unknown) {
  if (value === undefined) return;
  const block = document.createElement("details");
  block.className = "dev-block";
  const heading = document.createElement("summary");
  heading.className = "dev-block-label";
  heading.textContent = title;
  const pre = document.createElement("pre");
  pre.textContent = format(value);
  block.append(heading, pre);
  host.append(block);
}

function appendPayload(host: HTMLElement, trace: DevTrace) {
  const content = trace.content;
  if (trace.direction === "sent" && isRecord(content)) {
    if (Array.isArray(content.messages)) {
      for (const message of content.messages) {
        const role = isRecord(message) ? String(message.role ?? "message") : "message";
        const body = isRecord(message) ? message.content : message;
        appendBlock(host, role === "system" ? "System instructions" : `${role} message`, body);
      }
    }
    appendBlock(host, "System instructions", content.instructions);
    appendBlock(host, "Current-state brief", content.input);
    appendBlock(host, "Available tools", content.tools);
    const requestControls = Object.fromEntries(
      Object.entries(content).filter(([key]) =>
        !["messages", "instructions", "input", "tools"].includes(key)
      )
    );
    if (Object.keys(requestControls).length) {
      appendBlock(host, "Request settings", requestControls);
    }
    appendBlock(host, "Raw OpenAI request", {
      method: trace.method ?? "POST",
      endpoint: trace.endpoint ?? "/v1/responses",
      body: content,
    });
    return;
  }
  if (trace.direction === "received" && isRecord(content)) {
    if (content.object === "response" || Array.isArray(content.output)) {
      appendBlock(host, "Raw OpenAI response", content);
      return;
    }
    appendBlock(host, "Assistant text", content.assistant_content);
    appendBlock(host, "Tool call", content.tool_call);
    const remainder = Object.fromEntries(
      Object.entries(content).filter(([key]) =>
        !["assistant_content", "tool_call"].includes(key)
      )
    );
    if (Object.keys(remainder).length) appendBlock(host, "Response", remainder);
    return;
  }
  appendBlock(host, trace.direction === "error" ? "Error" : "Details", detailPayload(trace));
}

function parseRecord(value: unknown): Record<string, unknown> | null {
  if (isRecord(value)) return value;
  if (typeof value !== "string") return null;
  try {
    const parsed = JSON.parse(value);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function functionCall(content: unknown): Record<string, unknown> | null {
  if (!isRecord(content)) return null;
  if (isRecord(content.tool_call)) return content.tool_call;
  if (!Array.isArray(content.output)) return null;
  return content.output.find((item) => isRecord(item) && item.type === "function_call") as
    Record<string, unknown> | undefined ?? null;
}

function outputRecord(content: unknown): Record<string, unknown> | null {
  if (!isRecord(content)) return null;
  if (typeof content.output_text === "string") return parseRecord(content.output_text);
  if (!Array.isArray(content.output)) return null;
  for (const item of content.output) {
    if (!isRecord(item) || !Array.isArray(item.content)) continue;
    for (const part of item.content) {
      if (isRecord(part) && typeof part.text === "string") {
        const parsed = parseRecord(part.text);
        if (parsed) return parsed;
      }
    }
  }
  return null;
}

function words(value: unknown): string {
  return String(value ?? "").replace(/_/g, " ");
}

function compactNumber(value: unknown): string {
  const number = Number(value ?? 0);
  return number >= 1000 ? `${(number / 1000).toFixed(number >= 10000 ? 0 : 1)}k` : String(number);
}

function clipped(value: unknown, length = 130): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

function actionDigest(content: unknown): string | null {
  const call = functionCall(content);
  if (!call) return null;
  const args = parseRecord(call.arguments) ??
    (isRecord(call.arguments) ? call.arguments : {}) ?? {};
  const action = String(args.action ?? call.name ?? "action");
  const details: string[] = [];
  for (const key of ["weapon", "target", "domain", "resource"]) {
    if (args[key] != null) details.push(words(args[key]));
  }
  const move = details.length ? `${words(action)} · ${details.join(" → ")}` : words(action);
  const message = clipped(args.message, 96);
  return message ? `${move} — “${message}”` : move;
}

function rulingDigest(content: unknown): string | null {
  const result = outputRecord(content) ?? (isRecord(content) ? content : null);
  if (!result || !Array.isArray(result.rulings)) return null;
  const coherent = result.rulings.filter((r) => isRecord(r) && r.coherent !== false).length;
  const tension = Number(result.tension_delta ?? 0);
  const condemned = result.condemned ? ` · condemned ${words(result.condemned)}` : "";
  const bulletin = clipped(result.bulletin, 90);
  return `${coherent}/${result.rulings.length} actions coherent · tension ${tension >= 0 ? "+" : ""}${tension}${condemned}${bulletin ? ` — ${bulletin}` : ""}`;
}

function digestFor(trace: DevTrace): string {
  if (trace.direction === "sent") {
    return trace.agent === "arbiter"
      ? "Reviewing both declared actions against the true game state"
      : `Choosing the next action · ${trace.chain_reset === false ? "continuing context" : "fresh context"}`;
  }
  if (trace.direction === "received") {
    return (trace.agent === "arbiter" ? rulingDigest(trace.content) : actionDigest(trace.content))
      ?? "Response received · open for details";
  }
  if (trace.direction === "usage") {
    const cached = Number(trace.cached_input_tokens ?? 0);
    return `${compactNumber(trace.input_tokens)} input · ${compactNumber(trace.output_tokens)} output${cached ? ` · ${compactNumber(cached)} cached` : ""}`;
  }
  if (trace.direction === "error") {
    const content = isRecord(trace.content) ? trace.content : {};
    return clipped(content.message ?? trace.content ?? "The request failed; scripted fallback used");
  }
  if (trace.direction === "status") return clipped(trace.content ?? "Runtime status changed");
  return clipped(detailPayload(trace)) || "Open for details";
}

function titleFor(trace: DevTrace): string {
  return {
    sent: "Prompt sent",
    received: "Model response",
    usage: "Token usage",
    error: "Model error · scripted fallback used",
    status: "Runtime status",
  }[trace.direction] ?? trace.direction;
}

function renderEntry(trace: DevTrace): HTMLElement {
  const card = document.createElement("details");
  card.className = `dev-entry ${trace.direction}`;
  card.open = trace.direction === "error";

  const summary = document.createElement("summary");
  const copy = document.createElement("span");
  copy.className = "dev-entry-copy";
  const title = document.createElement("span");
  title.className = "dev-entry-title";
  title.textContent = titleFor(trace);
  const digest = document.createElement("span");
  digest.className = "dev-entry-digest";
  digest.textContent = digestFor(trace);
  const meta = document.createElement("span");
  meta.className = "dev-entry-meta";
  const elapsed = trace.elapsed_ms != null ? ` · ${trace.elapsed_ms}ms` : "";
  meta.textContent = `turn ${trace.turn ?? 0} · ${trace.api ?? "runtime"}${elapsed}`;
  copy.append(title, digest);
  summary.append(copy, meta);

  const payload = document.createElement("div");
  payload.className = "dev-payload";
  appendPayload(payload, trace);
  card.append(summary, payload);
  return card;
}

function render() {
  for (const agent of agents) {
    const tab = $<HTMLButtonElement>(`dev-tab-${agent}`);
    tab.classList.toggle("on", agent === active);
    tab.setAttribute("aria-selected", String(agent === active));
    tab.querySelector("span")!.textContent = String(traces[agent].length);
  }

  const latest = traces[active][traces[active].length - 1];
  const model = panel[active] ?? latest?.model ?? "waiting for first request";
  $("dev-agent-name").textContent = label(active);
  $("dev-model").textContent = `${provider || "openai"} · ${model}`;

  const host = $("dev-stream");
  const wasNearBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 80;
  host.replaceChildren();
  if (!traces[active].length) {
    const empty = document.createElement("div");
    empty.className = "dev-empty";
    empty.textContent = enabled
      ? "Waiting for this agent's next model request…"
      : "Enable Dev View to capture new model requests.";
    host.append(empty);
    return;
  }
  for (const trace of traces[active]) host.append(renderEntry(trace));
  if (wasNearBottom) host.scrollTop = host.scrollHeight;
}

function setEnabled(next: boolean, notify = true) {
  enabled = Boolean(next && available);
  $("dev-panel").classList.toggle("hidden", !enabled);
  $("btn-dev").classList.toggle("on", enabled);
  $("btn-dev").setAttribute("aria-pressed", String(enabled));
  if (notify) sender?.("dev_view", { enabled });
  render();
}

export function initDevView(send: Sender) {
  sender = send;
  $("btn-dev").onclick = () => setEnabled(!enabled);
  $("btn-dev-close").onclick = () => setEnabled(false);
  $("btn-dev-clear").onclick = () => {
    for (const agent of agents) traces[agent] = [];
    render();
  };
  for (const agent of agents) {
    $<HTMLButtonElement>(`dev-tab-${agent}`).onclick = () => {
      active = agent;
      render();
    };
  }
}

export function configureDevView(payload: Record<string, any>) {
  available = Boolean(payload.dev_view_available) && !payload.replay;
  provider = String(payload.llm_provider ?? provider);
  panel = payload.panel ?? panel;
  $("btn-dev").classList.toggle("hidden", !available);
  for (const agent of agents) traces[agent] = [];
  if (!available) setEnabled(false, false);
  else if (enabled) sender?.("dev_view", { enabled: true });
  render();
}

export function handleDevStatus(ev: GameEvent) {
  available = Boolean(ev.payload.available);
  provider = String(ev.payload.provider ?? provider);
  panel = ev.payload.panel ?? panel;
  setEnabled(Boolean(ev.payload.enabled), false);
}

export function pushDevTrace(ev: GameEvent) {
  const trace = ev.payload as DevTrace;
  if (!agents.includes(trace.agent)) return;
  traces[trace.agent].push(trace);
  if (traces[trace.agent].length > 120) traces[trace.agent].shift();
  render();
}
