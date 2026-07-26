import type { Effects, GameState, Nation, Side } from "./types";
import { WEAPON_BY_ID } from "./types";
import {
  batteries,
  buildIsland,
  coastAt,
  elevation,
  inland,
  launchSite,
  pickTarget,
  siteKey,
} from "./terrain";
import type { IslandMap, IslandStyle, Site, SiteRole } from "./terrain";

/**
 * The theatre.
 *
 * Two countries, a strait, and ordnance crossing it. The map is generated once per
 * nation (see terrain.ts) and painted into an offscreen bitmap that only changes when
 * the canvas resizes; everything that moves — weather, city lights, fires, smoke,
 * interceptors, tracers — is composited over it each frame.
 *
 * The rule that keeps the animation honest: a strike is aimed at a *place*. The engine
 * says "civilian" and the renderer picks the least-damaged town, flies the round to it,
 * craters it, and leaves it burning for the rest of the war. Nothing here invents
 * damage — it only draws where the damage the engine already applied actually landed.
 */

const FLAT = 0.72;            // islands are drawn squashed, as if seen from an angle
// Brass, gunmetal and phosphor — the three instrument colours of the olive theme.
const DOMAIN_COLOR: Record<string, string> = {
  air: "#d8a63f",
  naval: "#7d9ab0",
  cyber: "#8fd46a",
};

/** Where a nation's island sits on screen this frame. */
interface Place {
  cx: number;
  cy: number;
  r: number;
}

interface Scar {
  x: number;
  y: number;
  r: number;
  nuclear: boolean;
}

interface Fire {
  x: number;
  y: number;
  r: number;
  born: number;
  life: number;
  seed: number;
}

interface Smoke {
  side: Side;
  x: number;
  y: number;
  born: number;
  life: number;
  rise: number;
  drift: number;
  size: number;
}

/** Everything the renderer knows about one country that is not in the game state. */
interface Theatre {
  map: IslandMap;
  base: HTMLCanvasElement | null;
  baseKey: string;
  damage: Map<string, number>;
  scars: Scar[];
  fires: Fire[];
}

interface Round {
  /** Fraction along the path at which an interceptor killed this round, or 0. */
  killedAt: number;
  battery: number;
}

interface Tracer {
  from: Side;
  weapon: string;
  domain: string;
  born: number;
  launch: [number, number];
  target: Site;
  rounds: Round[];
  landed: boolean;
}

interface Fx {
  life: number;     // total ms the shot stays on screen
  flight: number;   // ms for one round to cross the strait
  count: number;    // rounds in the salvo
  stagger: number;  // ms between rounds leaving
  arc: number;      // peak height, as a fraction of canvas height
  color: string;
  style: "swarm" | "missile" | "barrage" | "cyber" | "nuke";
}

/** Each weapon flies differently. Shape and cadence say more than colour does. */
const FX: Record<string, Fx> = {
  // Many small craft, loose formation, slow and low.
  drone_swarm: { life: 4200, flight: 2600, count: 7, stagger: 150, arc: 0.13, color: "#ffb347", style: "swarm" },
  // One dart, flat and fast.
  cruise_missile: { life: 2600, flight: 1500, count: 1, stagger: 0, arc: 0.08, color: "#ff7a45", style: "missile" },
  // Shells lobbed on a high ballistic arc, landing in sequence.
  naval_barrage: { life: 4200, flight: 2000, count: 4, stagger: 420, arc: 0.26, color: "#4fb0ff", style: "barrage" },
  // No physical round at all — packets down a wire.
  cyber_strike: { life: 3000, flight: 1700, count: 6, stagger: 110, arc: 0, color: "#7dffa8", style: "cyber" },
  nuke: { life: 9000, flight: 3200, count: 1, stagger: 0, arc: 0.32, color: "#fff4d0", style: "nuke" },
};

const TARGET_ROLE: Record<string, SiteRole> = {
  military: "military",
  infrastructure: "infrastructure",
  civilian: "civilian",
};

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

/* ------------------------------------------------------------------ palette */

type RGB = [number, number, number];

/**
 * Hypsometric tints, and two mistakes worth not making again.
 *
 * The pale band sits right against 1: when it started at 0.74 every island came out
 * with an icecap in the middle of what was meant to be farmland. And zero is *green*,
 * not sand — most of an island's area is nowhere near a summit, so keying beach to low
 * elevation turned four fifths of both countries into desert. A beach is a coastal
 * thing, so it is blended in from distance-to-water instead.
 */
const RAMP: Array<[number, RGB]> = [
  [0.0, [88, 114, 66]],     // lowland
  [0.16, [104, 126, 72]],   // farmland and low forest
  [0.36, [124, 126, 74]],   // dry upland
  [0.58, [126, 110, 78]],   // scree
  [0.8, [122, 116, 110]],   // bare rock
  [1.0, [188, 190, 186]],   // summit
];
const SAND: RGB = [201, 183, 139];
const BEACH_WIDTH = 0.055;  // fraction of the island's radius, measured from the water

function terrainColour(h: number): RGB {
  for (let i = 1; i < RAMP.length; i++) {
    if (h <= RAMP[i][0]) {
      const [h0, c0] = RAMP[i - 1];
      const [h1, c1] = RAMP[i];
      const t = (h - h0) / (h1 - h0 || 1);
      return [lerp(c0[0], c1[0], t), lerp(c0[1], c1[1], t), lerp(c0[2], c1[2], t)];
    }
  }
  return RAMP[RAMP.length - 1][1];
}

const rgb = (c: RGB, k = 1) =>
  `rgb(${Math.round(c[0] * k)},${Math.round(c[1] * k)},${Math.round(c[2] * k)})`;

/* ------------------------------------------------------------------ the map */

export class WarMap {
  private ctx: CanvasRenderingContext2D;
  private tracers: Tracer[] = [];
  private smoke: Smoke[] = [];
  private state: GameState | null = null;
  private t = 0;
  private shake = 0;
  private flash = 0;
  private theatres: Partial<Record<Side, Theatre>> = {};

  constructor(private canvas: HTMLCanvasElement) {
    this.ctx = canvas.getContext("2d")!;
    this.resize();
    // The canvas now flexes to fill the viewport, so it can change size without the
    // window doing so — a plain resize listener would miss it.
    new ResizeObserver(() => this.resize()).observe(canvas);
    requestAnimationFrame(() => this.frame());
  }

  private resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.canvas.getBoundingClientRect();
    // Skip zero-size frames (flex layout settling) so we don't wipe a good bitmap.
    if (rect.width < 2 || rect.height < 2) return;
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  setState(state: GameState) {
    this.state = state;
    for (const side of ["west", "east"] as Side[]) {
      const terrain = state[side].terrain;
      const seed = Number(terrain?.seed ?? (side === "west" ? 1 : 2));
      const style = (terrain?.style ?? "delta") as IslandStyle;
      const existing = this.theatres[side];
      // Regenerate only when the country itself changes — never on a state echo, or
      // the coastline would crawl every turn.
      if (!existing || existing.map.style !== style || existing.baseKey.split("|")[0] !== String(seed)) {
        this.theatres[side] = {
          map: buildIsland(seed, style),
          base: null,
          baseKey: `${seed}|`,
          damage: new Map(),
          scars: [],
          fires: [],
        };
      }
    }
  }

  /** A new war: the countries survive, the craters do not. */
  clear() {
    for (const side of ["west", "east"] as Side[]) {
      const th = this.theatres[side];
      if (!th) continue;
      th.damage.clear();
      th.scars = [];
      th.fires = [];
    }
    this.tracers = [];
    this.smoke = [];
    this.shake = 0;
    this.flash = 0;
  }

  /**
   * Launch a salvo. The target is resolved here, once, so the ordnance and the fires
   * it starts agree about where it was going.
   *
   * `stopped` is not a suggestion and is not re-rolled: the engine has already decided
   * how many rounds the defender's batteries kill, has already subtracted them from the
   * damage, and has already told the commanders it did. The renderer's only job is to
   * show that many interceptions. It used to roll its own dice off the raw cover
   * number, which meant seven drones could visibly get through a wall that the
   * arithmetic said had stopped four of them.
   */
  fire(from: Side, weapon: string, target: string, stopped = 0, instant = false) {
    const to: Side = from === "west" ? "east" : "west";
    const attacker = this.theatres[from];
    const defender = this.theatres[to];
    if (!attacker || !defender) return;

    const domain = WEAPON_BY_ID[weapon]?.domain ?? "air";
    const fx = FX[weapon] ?? FX.drone_swarm;
    const site = pickTarget(defender.map, TARGET_ROLE[target] ?? "military", defender.damage);
    const pad = launchSite(attacker.map, domain);

    const guns = batteries(defender.map).length || 1;
    // Kill from the back of the salvo forward: the leaders are the ones that get
    // through, which is how a salvo that is being whittled down actually looks.
    const kills = Math.max(0, Math.min(fx.count - 1, Math.round(stopped)));
    const rounds: Round[] = [];
    for (let i = 0; i < fx.count; i++) {
      const killed = i >= fx.count - kills;
      rounds.push({
        killedAt: killed ? 0.62 + Math.random() * 0.22 : 0,
        battery: Math.floor(Math.random() * guns),
      });
    }

    // `instant` backdates the launch to the moment the round would already have arrived,
    // so the salvo lands on the next frame with no flight. Used when the bench is
    // fast-forwarding through a war: the scar and the fires belong to the turn being
    // jumped to, but nobody wants to watch eleven salvos cross the strait to get there.
    this.tracers.push({
      from, weapon, domain, born: performance.now() - (instant ? fx.flight : 0),
      launch: [pad.x, pad.y], target: site, rounds, landed: false,
    });
  }

  /* ------------------------------------------------------------- geometry */

  private place(side: Side, w: number, h: number): Place {
    // The centres are fixed at 23.5% / 76.5% because the speech bubbles and the damage
    // floaters are positioned in CSS against the same two numbers. The radius is capped
    // so that two islands at full coastal reach still leave water between them.
    const r = Math.min(w * 0.19, h * 0.42);
    return { cx: side === "west" ? w * 0.235 : w * 0.765, cy: h * 0.52, r };
  }

  private screen(p: Place, x: number, y: number): [number, number] {
    return [p.cx + x * p.r, p.cy + y * p.r * FLAT];
  }

  /* ------------------------------------------------------------- base bitmap */

  private coastPath(ctx: CanvasRenderingContext2D, map: IslandMap, p: Place) {
    ctx.beginPath();
    const n = map.coast.length;
    for (let i = 0; i <= n; i++) {
      const a = ((i % n) / n) * Math.PI * 2;
      const rad = map.coast[i % n];
      const [x, y] = this.screen(p, Math.cos(a) * rad, Math.sin(a) * rad);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
  }

  /**
   * Paint the country itself. Expensive — a hillshaded relief grid plus every road,
   * field, wood and building — so it happens once and is blitted from then on.
   */
  private buildBase(th: Theatre, p: Place): HTMLCanvasElement {
    const map = th.map;
    // Size the bitmap from the coastline this island actually has, not from a guessed
    // multiple of its radius. Six harmonics can sum to a reach of ~1.6, and at the
    // fixed 1.3 half-width Korsav's northern headland was being cropped off the top of
    // its own tile every single frame.
    const reach = Math.max(...map.coast) * 1.06;
    const cv = document.createElement("canvas");
    cv.width = Math.ceil(p.r * reach * 2) + 4;
    cv.height = Math.ceil(p.r * reach * FLAT * 2) + 4;
    const ctx = cv.getContext("2d")!;
    const local: Place = { cx: cv.width / 2, cy: cv.height / 2, r: p.r };
    const to = (x: number, y: number): [number, number] => [
      local.cx + x * local.r,
      local.cy + y * local.r * FLAT,
    ];

    ctx.save();
    this.coastPath(ctx, map, local);
    ctx.clip();

    // ---- relief. A coarse grid of shaded cells reads as a topographic map, and it
    // is the one thing that makes rivers and forests sit somewhere rather than float.
    const step = Math.max(2, p.r / 64);
    const sun: [number, number] = [-0.62, -0.78];
    for (let sy = -local.cy; sy < local.cy; sy += step) {
      for (let sx = -local.cx; sx < local.cx; sx += step) {
        const nx = sx / local.r;
        const ny = sy / (local.r * FLAT);
        const deep = inland(map, nx, ny);
        if (deep <= 0) continue;
        const h = elevation(map, nx, ny);
        // Sample the gradient at the cell size, not at a fixed distance: at a coarse
        // step a fine gradient just aliases into noise.
        const d = Math.max(0.012, step / local.r);
        const gx = (elevation(map, nx + d, ny) - elevation(map, nx - d, ny)) / (2 * d);
        const gy = (elevation(map, nx, ny + d) - elevation(map, nx, ny - d)) / (2 * d);
        const shade = clamp01(0.5 + 0.42 * (gx * sun[0] + gy * sun[1]));

        let col = terrainColour(h);
        if (deep < BEACH_WIDTH) {
          const t = 1 - deep / BEACH_WIDTH;
          col = [
            lerp(col[0], SAND[0], t), lerp(col[1], SAND[1], t), lerp(col[2], SAND[2], t),
          ];
        }
        // Capped: an unclamped Lambert term pushes the summit tint past white and the
        // high ground stops reading as ground at all.
        ctx.fillStyle = rgb(col, Math.min(1.06, 0.68 + 0.46 * shade));
        ctx.fillRect(local.cx + sx, local.cy + sy, step + 1, step + 1);
      }
    }

    // ---- farmland: a patchwork quilt on the flat ground.
    for (const f of map.farms) {
      const [x, y] = to(f.x, f.y);
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(f.angle);
      const green = f.tone > 0.5;
      ctx.fillStyle = green
        ? `rgba(126,146,74,${0.35 + f.tone * 0.25})`
        : `rgba(158,148,92,${0.3 + f.tone * 0.3})`;
      ctx.fillRect(-f.w * local.r / 2, -f.h * local.r * FLAT / 2, f.w * local.r, f.h * local.r * FLAT);
      ctx.strokeStyle = "rgba(40,44,30,0.28)";
      ctx.lineWidth = 0.6;
      ctx.strokeRect(-f.w * local.r / 2, -f.h * local.r * FLAT / 2, f.w * local.r, f.h * local.r * FLAT);
      ctx.restore();
    }

    // ---- rivers, widening as they near the sea.
    for (const river of map.rivers) {
      for (let i = 1; i < river.length; i++) {
        const [ax, ay] = to(river[i - 1][0], river[i - 1][1]);
        const [bx, by] = to(river[i][0], river[i][1]);
        const t = i / river.length;
        ctx.strokeStyle = `rgba(74,132,168,${0.55 + t * 0.35})`;
        ctx.lineWidth = Math.max(0.8, p.r * (0.004 + t * 0.012));
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.stroke();
      }
    }

    // ---- woodland.
    for (const wood of map.forests) {
      for (const [tx, ty, tr, shape] of wood.trees) {
        const [x, y] = to(tx, ty);
        const rr = tr * local.r;
        ctx.fillStyle = shape === 0 ? "#2f4a2c" : shape === 1 ? "#37542f" : "#294024";
        ctx.beginPath();
        if (shape === 2) {
          // Conifer.
          ctx.moveTo(x, y - rr * 1.5);
          ctx.lineTo(x + rr * 0.75, y + rr * 0.6);
          ctx.lineTo(x - rr * 0.75, y + rr * 0.6);
          ctx.closePath();
        } else {
          ctx.ellipse(x, y, rr, rr * 0.8, 0, 0, Math.PI * 2);
        }
        ctx.fill();
      }
    }

    // ---- roads: a dark casing under a pale surface, which is what makes a line on a
    // map read as a road rather than as a scratch.
    for (const pass of [0, 1]) {
      ctx.strokeStyle = pass === 0 ? "rgba(28,26,22,0.55)" : "rgba(216,204,176,0.72)";
      ctx.lineWidth = pass === 0 ? Math.max(2, p.r * 0.011) : Math.max(0.9, p.r * 0.005);
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      for (const r of map.roads) {
        ctx.beginPath();
        r.forEach(([x, y], i) => {
          const [sx, sy] = to(x, y);
          i === 0 ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy);
        });
        ctx.stroke();
      }
    }

    for (const site of map.sites) this.drawSite(ctx, site, local, to);

    ctx.restore();

    // ---- shoreline: a surf line just outside the coast and a hard edge on it.
    ctx.save();
    this.coastPath(ctx, map, local);
    ctx.strokeStyle = "rgba(228,236,208,0.45)";
    ctx.lineWidth = Math.max(1, p.r * 0.007);
    ctx.stroke();
    ctx.restore();

    return cv;
  }

  /** One built place, drawn from its parts. */
  private drawSite(
    ctx: CanvasRenderingContext2D,
    site: Site,
    p: Place,
    to: (x: number, y: number) => [number, number]
  ) {
    const [x, y] = to(site.x, site.y);
    const R = p.r;

    if (site.kind === "capital" || site.kind === "town") {
      // Built-up ground first, so the blocks sit on a city rather than on a field.
      ctx.fillStyle = "rgba(96,92,84,0.66)";
      ctx.beginPath();
      ctx.ellipse(x, y, site.size * R * 1.1, site.size * R * FLAT * 1.1, 0, 0, Math.PI * 2);
      ctx.fill();
      // Draw back-to-front so taller blocks nearer the viewer overlap the ones behind.
      const blocks = [...site.buildings].sort((a, b) => a.dy - b.dy);
      for (const b of blocks) {
        const bx = x + b.dx * R;
        const by = y + b.dy * R * FLAT;
        const bw = Math.max(2, b.w * R);
        const bh = Math.max(2, b.h * R * FLAT);
        const lift = (0.25 + b.tall) * R * 0.035;
        // Shadow, side wall, then a lit roof: enough parallax to read as a building.
        ctx.fillStyle = "rgba(24,22,20,0.4)";
        ctx.fillRect(bx + bw * 0.2, by + bh * 0.2, bw, bh);
        ctx.fillStyle = b.tall > 0.55 ? "#4a4e58" : "#3f434a";
        ctx.fillRect(bx, by - lift, bw, bh + lift);
        ctx.fillStyle = b.tall > 0.55 ? "#9aa2b2" : "#767d89";
        ctx.fillRect(bx, by - lift, bw, bh * 0.7);
      }
      return;
    }

    if (site.kind === "airbase") {
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(site.angle);
      const len = site.size * R * 1.9;
      const wide = site.size * R * 0.2;
      // Apron.
      ctx.fillStyle = "rgba(92,92,88,0.75)";
      ctx.fillRect(-len * 0.28, wide * 1.1, len * 0.56, wide * 1.5);
      // Runway with a dashed centreline.
      ctx.fillStyle = "#3a3d42";
      ctx.fillRect(-len / 2, -wide / 2, len, wide);
      ctx.strokeStyle = "rgba(236,236,228,0.75)";
      ctx.lineWidth = Math.max(0.6, wide * 0.11);
      ctx.setLineDash([wide * 0.9, wide * 0.9]);
      ctx.beginPath();
      ctx.moveTo(-len * 0.44, 0);
      ctx.lineTo(len * 0.44, 0);
      ctx.stroke();
      ctx.setLineDash([]);
      // Aircraft on the apron.
      ctx.fillStyle = "#c9cfd8";
      for (let i = -2; i <= 2; i++) {
        const px = i * len * 0.1;
        const py = wide * 1.85;
        ctx.beginPath();
        ctx.moveTo(px, py - wide * 0.34);
        ctx.lineTo(px + wide * 0.3, py + wide * 0.2);
        ctx.lineTo(px - wide * 0.3, py + wide * 0.2);
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
      return;
    }

    if (site.kind === "port") {
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(site.angle);
      const len = site.size * R;
      // Quay, then piers running out into the water.
      ctx.fillStyle = "#5b5b58";
      ctx.fillRect(-len * 0.16, -len * 0.55, len * 0.32, len * 1.1);
      ctx.fillStyle = "#6a6a66";
      for (let i = -1; i <= 1; i++) {
        ctx.fillRect(len * 0.1, i * len * 0.36 - len * 0.045, len * 0.85, len * 0.09);
      }
      // Moored hulls.
      ctx.fillStyle = "#2f3b48";
      for (let i = -1; i <= 1; i += 2) {
        ctx.beginPath();
        ctx.ellipse(len * 0.6, i * len * 0.19, len * 0.3, len * 0.07, 0, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
      return;
    }

    if (site.kind === "plant") {
      const s = site.size * R;
      ctx.fillStyle = "rgba(70,68,62,0.8)";
      ctx.fillRect(x - s * 0.5, y - s * 0.28, s, s * 0.56);
      // Cooling towers.
      ctx.fillStyle = "#9aa0a4";
      for (const dx of [-0.24, 0.1]) {
        ctx.beginPath();
        ctx.moveTo(x + dx * s - s * 0.13, y - s * 0.26);
        ctx.lineTo(x + dx * s + s * 0.13, y - s * 0.26);
        ctx.lineTo(x + dx * s + s * 0.09, y + s * 0.2);
        ctx.lineTo(x + dx * s - s * 0.09, y + s * 0.2);
        ctx.closePath();
        ctx.fill();
      }
      // Storage tanks.
      ctx.fillStyle = "#8b8f93";
      ctx.beginPath();
      ctx.arc(x + s * 0.36, y + s * 0.02, s * 0.13, 0, Math.PI * 2);
      ctx.fill();
      return;
    }

    // Battery: a dish and two launchers. Small, but it is where interceptors come from.
    const s = site.size * R;
    ctx.fillStyle = "rgba(58,64,58,0.9)";
    ctx.beginPath();
    ctx.arc(x, y, s * 0.9, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "rgba(190,210,190,0.8)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x - s * 0.5, y + s * 0.3);
    ctx.lineTo(x, y - s * 0.6);
    ctx.lineTo(x + s * 0.5, y + s * 0.3);
    ctx.stroke();
  }

  /* ------------------------------------------------------------- dynamic layers */

  /** Lights come on at dusk and go out as a country is wrecked or turns on itself. */
  private drawLights(ctx: CanvasRenderingContext2D, th: Theatre, p: Place, n: Nation) {
    const health = clamp01(n.integrity / 100);
    const calm = clamp01(1 - n.unrest / 130);
    const lit = health * calm;
    if (lit <= 0.02) return;

    for (const site of th.map.sites) {
      if (site.kind !== "capital" && site.kind !== "town") continue;
      const hurt = th.damage.get(siteKey(site)) ?? 0;
      const local = lit * (1 - hurt);
      for (let i = 0; i < site.buildings.length; i++) {
        if (i / site.buildings.length > local) continue;
        const b = site.buildings[i];
        const [x, y] = this.screen(p, site.x + b.dx, site.y + b.dy);
        // A slow per-window flicker so a city looks inhabited rather than printed.
        const flicker = 0.55 + 0.45 * Math.sin(this.t / 26 + i * 2.4);
        ctx.fillStyle = `rgba(255,214,140,${0.5 * flicker * local})`;
        ctx.fillRect(x + b.w * p.r * 0.25, y - b.tall * p.r * 0.02, Math.max(1, b.w * p.r * 0.4), 1.4);
      }
    }
  }

  private drawScars(ctx: CanvasRenderingContext2D, th: Theatre, p: Place) {
    for (const s of th.scars) {
      const [x, y] = this.screen(p, s.x, s.y);
      const rr = s.r * p.r;
      const g = ctx.createRadialGradient(x, y, 0, x, y, rr);
      if (s.nuclear) {
        g.addColorStop(0, "rgba(28,22,18,0.95)");
        g.addColorStop(0.55, "rgba(64,48,36,0.8)");
        g.addColorStop(1, "rgba(70,56,40,0)");
      } else {
        g.addColorStop(0, "rgba(22,16,12,0.9)");
        g.addColorStop(0.6, "rgba(46,36,28,0.6)");
        g.addColorStop(1, "rgba(46,36,28,0)");
      }
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.ellipse(x, y, rr, rr * FLAT, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  private drawFires(ctx: CanvasRenderingContext2D, th: Theatre, p: Place, side: Side, now: number) {
    th.fires = th.fires.filter((f) => now - f.born < f.life);
    for (const f of th.fires) {
      const age = (now - f.born) / f.life;
      const [x, y] = this.screen(p, f.x, f.y);
      const rr = f.r * p.r * (1 - age * 0.45);
      // Emit smoke at a rate that falls off as the fire burns down.
      if (Math.random() < 0.32 * (1 - age) && this.smoke.length < 260) {
        this.smoke.push({
          side, x: f.x, y: f.y, born: now,
          life: 4200 + Math.random() * 3200,
          rise: 0.35 + Math.random() * 0.5,
          drift: (side === "west" ? 1 : -1) * (0.12 + Math.random() * 0.22),
          size: f.r * (1.2 + Math.random()),
        });
      }
      const flick = 0.65 + 0.35 * Math.sin(this.t / 3 + f.seed);
      const g = ctx.createRadialGradient(x, y, 0, x, y, rr * 2.4 * flick);
      g.addColorStop(0, `rgba(255,238,180,${0.85 * (1 - age)})`);
      g.addColorStop(0.35, `rgba(255,146,44,${0.6 * (1 - age)})`);
      g.addColorStop(1, "rgba(180,40,10,0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, rr * 2.4 * flick, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  private drawSmoke(ctx: CanvasRenderingContext2D, now: number, w: number, h: number) {
    this.smoke = this.smoke.filter((s) => now - s.born < s.life);
    for (const s of this.smoke) {
      const p = this.place(s.side, w, h);
      const age = (now - s.born) / s.life;
      const [bx, by] = this.screen(p, s.x + s.drift * age, s.y);
      // Columns rise in screen space, not island space — smoke does not respect the
      // squashed perspective the land is drawn in.
      const y = by - age * s.rise * p.r * 1.5;
      const rr = s.size * p.r * (0.5 + age * 2.6);
      ctx.fillStyle = `rgba(58,54,52,${0.34 * (1 - age) * (1 - age)})`;
      ctx.beginPath();
      ctx.ellipse(bx, y, rr, rr * 0.8, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  /**
   * The defence envelope.
   *
   * This used to be three faint dashed rings that changed almost imperceptibly as cover
   * rose, which meant the single most important thing a commander can do to the shape of
   * the war — harden the domain it keeps being hit through — was invisible. Now the ring
   * carries the number: a tick mark for every ten points of cover, brightening and
   * thickening with it, and a hardened domain gets a filled dome and a live radar sweep
   * out of its own batteries.
   */
  private drawDefences(
    ctx: CanvasRenderingContext2D, th: Theatre, p: Place, n: Nation
  ) {
    const fx: Effects = n.effects ?? {
      shield: {}, morale_buffer: 0, blockaded: 0, sanctioned: 0, spin: 0, deficit_turns: 0,
    };
    const domains = ["air", "naval", "cyber"];
    domains.forEach((d, i) => {
      const cover = n.defenses?.[d] ?? 0;
      const level = cover / 100;
      const covered = (fx.shield?.[d] ?? 0) > 0;
      if (cover <= 2 && !covered) return;

      // Kept close to the coast: further out and three concentric rings read as
      // planetary orbits rather than as an air defence envelope.
      const rx = p.r * (1.1 + i * 0.075);
      const ry = p.r * FLAT * (1.14 + i * 0.09);
      const pulse = 0.5 + 0.5 * Math.sin(this.t / 16 + i);

      // A hardened domain is a dome, not a line — you should be able to see at a glance
      // that the next salvo through it is going to be eaten.
      if (covered) {
        ctx.save();
        ctx.globalAlpha = 0.06 + 0.04 * pulse;
        ctx.fillStyle = DOMAIN_COLOR[d];
        ctx.beginPath();
        ctx.ellipse(p.cx, p.cy, rx, ry, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      ctx.save();
      ctx.beginPath();
      ctx.ellipse(p.cx, p.cy, rx, ry, 0, 0, Math.PI * 2);
      ctx.strokeStyle = DOMAIN_COLOR[d];
      ctx.globalAlpha = covered ? 0.5 + 0.2 * pulse : 0.1 + level * 0.45;
      ctx.setLineDash(covered ? [] : [4, 7]);
      ctx.lineWidth = covered ? 2.6 : 0.9 + level * 2;
      ctx.stroke();
      ctx.setLineDash([]);

      // Countable strength: one tick per ten points of cover. A defence you can read
      // off the map is a defence worth spending a turn on.
      const ticks = Math.round(cover / 10);
      ctx.globalAlpha = covered ? 0.85 : 0.25 + level * 0.5;
      ctx.lineWidth = 1.6;
      for (let k = 0; k < ticks; k++) {
        const a = (k / Math.max(1, ticks)) * Math.PI * 2 + i * 0.4 + this.t / 900;
        const cos = Math.cos(a);
        const sin = Math.sin(a);
        ctx.beginPath();
        ctx.moveTo(p.cx + cos * rx, p.cy + sin * ry);
        ctx.lineTo(p.cx + cos * (rx + 5), p.cy + sin * (ry + 5));
        ctx.stroke();
      }
      ctx.restore();
    });

    // A live sweep out of each battery, so the guns are visibly awake between salvoes.
    const guns = batteries(th.map);
    if (!guns.length) return;
    const reach = ((n.defenses?.air ?? 0) + (n.defenses?.naval ?? 0)) / 200;
    if (reach <= 0.05) return;
    ctx.save();
    ctx.globalAlpha = 0.16 + 0.08 * Math.sin(this.t / 30);
    ctx.strokeStyle = DOMAIN_COLOR.air;
    ctx.lineWidth = 1;
    guns.forEach((gun, k) => {
      const [gx, gy] = this.screen(p, gun.x, gun.y);
      const a = this.t / 34 + k * 2.1;
      const len = p.r * (0.18 + reach * 0.34);
      ctx.beginPath();
      ctx.moveTo(gx, gy);
      ctx.lineTo(gx + Math.cos(a) * len, gy + Math.sin(a) * len * FLAT);
      ctx.stroke();
    });
    ctx.restore();
  }

  /** A blockade is hulls on the water, not a dashed line. */
  private drawBlockade(ctx: CanvasRenderingContext2D, p: Place, n: Nation) {
    if ((n.effects?.blockaded ?? 0) <= 0) return;
    const ships = 10;
    for (let i = 0; i < ships; i++) {
      const a = (i / ships) * Math.PI * 2 + this.t / 1400;
      // Close in, on the shipping lanes — a cordon out in the middle of the strait
      // reads as two fleets loose in the ocean rather than as a port being closed.
      const x = p.cx + Math.cos(a) * p.r * 1.24;
      const y = p.cy + Math.sin(a) * p.r * FLAT * 1.34;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(Math.cos(a) * 0.25 + Math.sin(this.t / 40 + i) * 0.05);
      ctx.fillStyle = "#7d3535";
      ctx.beginPath();
      ctx.ellipse(0, 0, p.r * 0.028, p.r * 0.008, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillRect(-p.r * 0.005, -p.r * 0.017, p.r * 0.01, p.r * 0.017);
      ctx.strokeStyle = "rgba(200,220,255,0.14)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(-p.r * 0.032, 0);
      ctx.lineTo(-p.r * 0.085, 0);
      ctx.stroke();
      ctx.restore();
    }
  }

  /* ------------------------------------------------------------- ordnance */

  private impact(tracer: Tracer, defender: Theatre, screenR: number) {
    if (tracer.landed) return;
    tracer.landed = true;
    const site = tracer.target;
    const nuclear = tracer.weapon === "nuke";
    const key = siteKey(site);
    const hurt = Math.min(1, (defender.damage.get(key) ?? 0) + (nuclear ? 1 : 0.34));
    defender.damage.set(key, hurt);

    const spread = nuclear ? site.size * 2.6 : site.size * 0.8;
    defender.scars.push({
      x: site.x + (Math.random() - 0.5) * spread * 0.4,
      y: site.y + (Math.random() - 0.5) * spread * 0.4,
      r: nuclear ? site.size * 2.4 : site.size * (0.4 + Math.random() * 0.35),
      nuclear,
    });

    const count = nuclear ? 10 : 3;
    for (let i = 0; i < count; i++) {
      defender.fires.push({
        x: site.x + (Math.random() - 0.5) * spread,
        y: site.y + (Math.random() - 0.5) * spread * 0.7,
        r: (nuclear ? 0.05 : 0.02) + Math.random() * 0.02,
        born: performance.now(),
        life: nuclear ? 60000 : 22000 + Math.random() * 14000,
        seed: Math.random() * 100,
      });
    }
    // Cap the burn so a twelve-turn war does not end at 200 fires and 8fps.
    if (defender.fires.length > 26) defender.fires.splice(0, defender.fires.length - 26);
    if (defender.scars.length > 40) defender.scars.splice(0, defender.scars.length - 40);

    this.shake = Math.min(1, this.shake + (nuclear ? 1 : 0.28 + screenR * 0));
    if (nuclear) this.flash = 1;
  }

  /** Draw round `i` of a salvo, and the interceptor that may be coming up to meet it. */
  private round(
    tr: Tracer, fx: Fx, i: number, p: number,
    from: [number, number], to: [number, number],
    defender: Theatre, defPlace: Place, h: number, w: number, scale: number
  ) {
    const ctx = this.ctx;
    const round = tr.rounds[i];
    const dir = to[0] > from[0] ? 1 : -1;
    const lane = i - (fx.count - 1) / 2;

    // An intercepted round never reaches its target; it dies where the missile met it.
    const kill = round.killedAt;
    const live = kill > 0 ? Math.min(p, kill) : p;

    const x = lerp(from[0], to[0], live);
    let y = lerp(from[1], to[1], live) - Math.sin(live * Math.PI) * h * fx.arc;
    const impact = kill === 0 && p > 0.93 ? (p - 0.93) / 0.07 : -1;

    ctx.save();

    if (fx.style === "swarm") {
      y += lane * scale * 9 + Math.sin(live * 9 + i) * scale * 4;
      ctx.fillStyle = fx.color;
      ctx.globalAlpha = 0.55 + 0.45 * Math.sin(live * Math.PI);
      ctx.beginPath();
      ctx.moveTo(x + 6 * dir * scale, y);
      ctx.lineTo(x - 4 * dir * scale, y - 3.5 * scale);
      ctx.lineTo(x - 1 * dir * scale, y);
      ctx.lineTo(x - 4 * dir * scale, y + 3.5 * scale);
      ctx.closePath();
      ctx.fill();
    } else if (fx.style === "missile") {
      const tail = ctx.createLinearGradient(x - 80 * dir * scale, y, x, y);
      tail.addColorStop(0, "rgba(255,122,69,0)");
      tail.addColorStop(1, fx.color);
      ctx.strokeStyle = tail;
      ctx.lineWidth = 3 * scale;
      ctx.beginPath();
      ctx.moveTo(x - 80 * dir * scale, y + 6 * scale);
      ctx.lineTo(x, y);
      ctx.stroke();
      ctx.fillStyle = "#fff2e2";
      ctx.beginPath();
      ctx.moveTo(x + 7 * dir * scale, y);
      ctx.lineTo(x - 4 * dir * scale, y - 2.5 * scale);
      ctx.lineTo(x - 4 * dir * scale, y + 2.5 * scale);
      ctx.closePath();
      ctx.fill();
    } else if (fx.style === "barrage") {
      ctx.fillStyle = fx.color;
      ctx.beginPath();
      ctx.arc(x, y, 4 * scale, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 0.16;
      ctx.strokeStyle = fx.color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(from[0], from[1]);
      ctx.quadraticCurveTo((from[0] + to[0]) / 2, from[1] - h * fx.arc * 2, x, y);
      ctx.stroke();
    } else if (fx.style === "cyber") {
      // Packets down a wire: dashes on a dead-straight line, no ballistics at all.
      const yy = lerp(from[1], to[1], live) - 4 * scale + lane * 5 * scale;
      ctx.strokeStyle = fx.color;
      ctx.globalAlpha = 0.85;
      ctx.lineWidth = 2 * scale;
      ctx.setLineDash([9 * scale, 7 * scale]);
      ctx.lineDashOffset = -live * 60;
      ctx.beginPath();
      ctx.moveTo(x - 26 * dir * scale, yy);
      ctx.lineTo(x, yy);
      ctx.stroke();
      ctx.setLineDash([]);
    } else {
      // Nuclear: a single bright object on a high, slow arc.
      ctx.fillStyle = fx.color;
      ctx.beginPath();
      ctx.arc(x, y, 5 * scale, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 0.5 * (1 - live);
      ctx.strokeStyle = fx.color;
      ctx.lineWidth = 2.5 * scale;
      ctx.beginPath();
      ctx.moveTo(from[0], from[1]);
      ctx.quadraticCurveTo((from[0] + to[0]) / 2, from[1] - h * 0.7, x, y);
      ctx.stroke();
    }
    ctx.restore();

    // ---- interception. The missile comes up from a real battery, so a blunted strike
    // has a visible cause instead of just a smaller number.
    if (kill > 0) {
      const guns = batteries(defender.map);
      const gun = guns[round.battery % Math.max(1, guns.length)];
      if (gun) {
        const [gx, gy] = this.screen(defPlace, gun.x, gun.y);
        const rise = clamp01((p - kill * 0.45) / (kill * 0.55 || 1));
        ctx.save();
        ctx.globalAlpha = 0.8 * (1 - Math.max(0, p - kill) * 3);
        ctx.strokeStyle = "#d8f2ff";
        ctx.lineWidth = 1.6 * scale;
        ctx.beginPath();
        ctx.moveTo(gx, gy);
        ctx.lineTo(lerp(gx, x, rise), lerp(gy, y, rise));
        ctx.stroke();
        ctx.restore();
      }
      if (p > kill) {
        const burst = Math.min(1, (p - kill) * 5);
        ctx.save();
        ctx.globalAlpha = 0.9 * (1 - burst);
        const g = ctx.createRadialGradient(x, y, 0, x, y, 26 * scale * burst);
        g.addColorStop(0, "rgba(255,255,235,0.95)");
        g.addColorStop(1, "rgba(255,190,90,0)");
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(x, y, 26 * scale * burst, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
      return;
    }

    if (impact <= 0) return;

    // ---- what landing looks like.
    ctx.save();
    if (fx.style === "nuke") {
      const g = ctx.createRadialGradient(to[0], to[1], 0, to[0], to[1], defPlace.r * 2.4 * impact);
      g.addColorStop(0, `rgba(255,255,245,${1 - impact})`);
      g.addColorStop(0.4, `rgba(255,180,80,${0.8 * (1 - impact)})`);
      g.addColorStop(1, "rgba(255,80,30,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
      ctx.globalAlpha = 0.8 * (1 - impact);
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 3 * (1 - impact) * scale;
      ctx.beginPath();
      ctx.ellipse(to[0], to[1], defPlace.r * 3 * impact, defPlace.r * FLAT * 3 * impact, 0, 0, Math.PI * 2);
      ctx.stroke();
    } else if (fx.style === "cyber") {
      // Nothing explodes. The lights go out and the screens fill with noise.
      ctx.globalAlpha = 0.5 * (1 - impact);
      ctx.fillStyle = fx.color;
      for (let b = 0; b < 6; b++) {
        const by = to[1] - defPlace.r * 0.4 + Math.random() * defPlace.r * 0.8;
        ctx.fillRect(to[0] - defPlace.r * 0.4, by, defPlace.r * 0.8 * Math.random(), 2);
      }
    } else {
      // Flash, then a shockwave ring flattened into the ground plane, then dust.
      const g = ctx.createRadialGradient(to[0], to[1], 0, to[0], to[1], defPlace.r * 0.7 * impact);
      g.addColorStop(0, `rgba(255,244,214,${0.95 * (1 - impact)})`);
      g.addColorStop(0.5, `rgba(255,150,50,${0.5 * (1 - impact)})`);
      g.addColorStop(1, "rgba(255,110,40,0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(to[0], to[1], defPlace.r * 0.7 * impact, 0, Math.PI * 2);
      ctx.fill();

      ctx.globalAlpha = 0.55 * (1 - impact);
      ctx.strokeStyle = "#ffe6c0";
      ctx.lineWidth = 2 * (1 - impact) * scale;
      ctx.beginPath();
      ctx.ellipse(
        to[0], to[1],
        defPlace.r * 0.55 * impact, defPlace.r * FLAT * 0.55 * impact,
        0, 0, Math.PI * 2
      );
      ctx.stroke();
    }
    ctx.restore();
  }

  /* ------------------------------------------------------------- frame */

  private frame() {
    const ctx = this.ctx;
    const rect = this.canvas.getBoundingClientRect();
    const w = rect.width;
    const h = rect.height;
    this.t += 1;

    // Layout not ready yet — keep the rAF loop alive without painting garbage.
    if (w < 2 || h < 2) {
      requestAnimationFrame(() => this.frame());
      return;
    }

    const now = performance.now();
    const tension = this.state?.world.tension ?? 0;

    ctx.save();
    if (this.shake > 0.01) {
      ctx.translate(
        (Math.random() - 0.5) * this.shake * 14,
        (Math.random() - 0.5) * this.shake * 14
      );
      this.shake *= 0.9;
    }

    // ---- ocean. Deep slate-green rather than blue, so the water belongs to the same
    // olive world as the panels around it. It warms towards brass as tension climbs.
    const sea = ctx.createLinearGradient(0, 0, 0, h);
    sea.addColorStop(0, `rgb(${14 + tension * 0.24}, ${28 + tension * 0.1}, ${28})`);
    sea.addColorStop(1, `rgb(${6 + tension * 0.14}, ${13}, ${14})`);
    ctx.fillStyle = sea;
    ctx.fillRect(-20, -20, w + 40, h + 40);

    // Swell: long low sine bands that drift, so the water is never still.
    for (let i = 0; i < 9; i++) {
      const y = h * (0.06 + i * 0.11);
      ctx.beginPath();
      for (let x = 0; x <= w; x += 12) {
        const yy = y + Math.sin(x / 90 + this.t / 42 + i) * 3 + Math.sin(x / 33 - this.t / 70) * 1.4;
        x === 0 ? ctx.moveTo(x, yy) : ctx.lineTo(x, yy);
      }
      ctx.strokeStyle = `rgba(196,214,168,${0.026 + 0.011 * Math.sin(this.t / 60 + i)})`;
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    const west = this.state?.west ?? placeholder("west");
    const east = this.state?.east ?? placeholder("east");
    if (!this.theatres.west || !this.theatres.east) this.bootstrap();

    const places: Record<Side, Place> = {
      west: this.place("west", w, h),
      east: this.place("east", w, h),
    };
    const nations: Record<Side, Nation> = { west, east };
    const scale = Math.max(0.6, places.west.r / 150);

    for (const side of ["west", "east"] as Side[]) {
      const th = this.theatres[side]!;
      const p = places[side];

      // Shallows: a soft halo just off the coast, which is what makes an island look
      // like it is *in* water rather than pasted on top of it.
      ctx.save();
      // Follow the coastline rather than an ellipse — a circular halo around an
      // irregular island reads as a dinner plate the island is sitting on.
      ctx.filter = `blur(${Math.max(4, p.r * 0.06)}px)`;
      this.coastPath(ctx, th.map, { ...p, r: p.r * 1.1 });
      ctx.fillStyle = "rgba(120,158,140,0.28)";
      ctx.fill();
      ctx.filter = "none";
      ctx.restore();

      const key = `${th.baseKey.split("|")[0]}|${Math.round(p.r)}`;
      if (!th.base || th.baseKey !== key) {
        th.base = this.buildBase(th, p);
        th.baseKey = key;
      }
      ctx.drawImage(th.base, p.cx - th.base.width / 2, p.cy - th.base.height / 2);

      this.drawScars(ctx, th, p);
      this.drawLights(ctx, th, p, nations[side]);
      this.drawFires(ctx, th, p, side, now);
      this.drawDefences(ctx, th, p, nations[side]);
      this.drawBlockade(ctx, p, nations[side]);
    }

    this.drawSmoke(ctx, now, w, h);

    // ---- ordnance in flight.
    this.tracers = this.tracers.filter((tr) => now - tr.born < (FX[tr.weapon] ?? FX.drone_swarm).life);
    for (const tr of this.tracers) {
      const fx = FX[tr.weapon] ?? FX.drone_swarm;
      const toSide: Side = tr.from === "west" ? "east" : "west";
      const attacker = this.theatres[tr.from]!;
      const defender = this.theatres[toSide]!;
      const from = this.screen(places[tr.from], tr.launch[0], tr.launch[1]);
      const to = this.screen(places[toSide], tr.target.x, tr.target.y);
      const elapsed = now - tr.born;

      for (let i = 0; i < fx.count; i++) {
        // Each round in a salvo leaves slightly after the one before it.
        const p = (elapsed - i * fx.stagger) / fx.flight;
        if (p < 0 || p > 1.08) continue;
        this.round(
          tr, fx, i, Math.min(p, 1), from, to,
          defender, places[toSide], h, w, scale
        );
        if (p >= 0.995 && tr.rounds[i].killedAt === 0) {
          this.impact(tr, defender, places[toSide].r);
        }
      }
      void attacker;
    }

    // ---- names.
    ctx.font = `600 ${Math.round(11 * scale)}px ui-monospace, monospace`;
    ctx.textAlign = "center";
    for (const side of ["west", "east"] as Side[]) {
      const p = places[side];
      ctx.fillStyle = "rgba(8,12,20,0.6)";
      ctx.fillText(nations[side].name.toUpperCase(), p.cx + 1, p.cy + p.r * FLAT + 27);
      ctx.fillStyle = "rgba(232,238,248,0.9)";
      ctx.fillText(nations[side].name.toUpperCase(), p.cx, p.cy + p.r * FLAT + 26);
    }

    ctx.restore();

    // ---- nuclear whiteout, over everything including the shake.
    if (this.flash > 0.01) {
      ctx.fillStyle = `rgba(255,250,238,${this.flash})`;
      ctx.fillRect(0, 0, w, h);
      this.flash *= 0.94;
    }

    requestAnimationFrame(() => this.frame());
  }

  /** Islands before the first state arrives, so the theatre is never empty. */
  private bootstrap() {
    if (!this.theatres.west) {
      this.theatres.west = blankTheatre(buildIsland(20260726, "delta"), 20260726);
    }
    if (!this.theatres.east) {
      this.theatres.east = blankTheatre(buildIsland(77415, "highland"), 77415);
    }
  }
}

function blankTheatre(map: IslandMap, seed: number): Theatre {
  return { map, base: null, baseKey: `${seed}|`, damage: new Map(), scars: [], fires: [] };
}

function placeholder(side: Side): Nation {
  return {
    side,
    name: side === "west" ? "Aurelia" : "Korsav",
    integrity: 100, morale: 60, military: 70, standing: 70,
    gdp: 70, gdp_base: 70, budget: 80,
    intl_pressure: 0, unrest: 10, propaganda: 30, casualties: 0,
    defenses: { air: 20, naval: 20, cyber: 20 },
    arsenal: {}, strike_streak: 0,
    effects: {
      shield: {}, morale_buffer: 0, blockaded: 0,
      sanctioned: 0, spin: 0, deficit_turns: 0,
    },
    terrain: { seed: side === "west" ? 20260726 : 77415, style: side === "west" ? "delta" : "highland" },
  };
}

// Kept for the shore-noise helper in terrain, which the renderer imports for typing.
void coastAt;
