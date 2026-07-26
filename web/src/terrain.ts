/**
 * Island generation.
 *
 * The map used to be a wobbly blob with a colour ramp for damage, which meant a strike
 * landed on nothing in particular and "integrity 62" was a number rather than a place.
 * This grows an actual country: relief, rivers running from the high ground to the sea,
 * forest, farmland, roads between towns, a capital, an airbase, a working port.
 *
 * Two rules make the rest of the app simple:
 *   · everything is generated in normalised island space — x and y in roughly -1..1 —
 *     so the renderer owns all scaling and a resize never regenerates the country.
 *   · every strike-able thing is a `Site` with a `role`, so the engine's three target
 *     types (military / infrastructure / civilian) each map onto real places you can
 *     watch burn.
 *
 * Generation is seeded, so a nation looks the same in every match it fights.
 */

export type SiteKind =
  | "capital" | "town" | "airbase" | "port" | "plant" | "battery" | "bridge";

/** What the engine's `target` argument means on the ground. */
export type SiteRole = "civilian" | "military" | "infrastructure";

export interface Building {
  dx: number;
  dy: number;
  w: number;
  h: number;
  /** Tall blocks read as a downtown; short ones as a suburb. */
  tall: number;
}

export interface Site {
  kind: SiteKind;
  role: SiteRole;
  x: number;
  y: number;
  /** Rough footprint radius, in island space. Impacts scale off it. */
  size: number;
  /** Runways, piers and bridges are oriented; everything else ignores this. */
  angle: number;
  buildings: Building[];
  name: string;
}

export interface Forest {
  x: number;
  y: number;
  /** x, y, radius, and a shape index so not every tree is the same tree. */
  trees: Array<[number, number, number, number]>;
}

export interface Farm {
  x: number;
  y: number;
  w: number;
  h: number;
  angle: number;
  tone: number;
}

export interface Peak {
  x: number;
  y: number;
  h: number;
  r: number;
}

export interface IslandMap {
  /** Coast radius sampled at `coast.length` evenly spaced angles. */
  coast: number[];
  /** Seeds the ridge noise laid over the landform. */
  noiseSeed: number;
  peaks: Peak[];
  rivers: Array<Array<[number, number]>>;
  roads: Array<Array<[number, number]>>;
  forests: Forest[];
  farms: Farm[];
  sites: Site[];
  style: IslandStyle;
}

export type IslandStyle = "delta" | "highland";

/* ------------------------------------------------------------------ randomness */

/** mulberry32: tiny, seedable, and good enough that coastlines do not look periodic. */
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Rand = () => number;

const TAU = Math.PI * 2;
const between = (r: Rand, lo: number, hi: number) => lo + r() * (hi - lo);

/* ------------------------------------------------------------------ coastline */

const COAST_SAMPLES = 256;

/**
 * A radial coastline: a base circle plus a few harmonics. Layering three low
 * frequencies gives headlands and bays; a fourth high one gives the ragged edge that
 * reads as rock rather than as a rounded rectangle.
 */
function makeCoast(r: Rand, style: IslandStyle): number[] {
  const waves = [
    // k=1 makes one end of the island fatter than the other, which is most of what
    // stops a generated coastline reading as a lozenge.
    { k: 1, amp: between(r, 0.07, 0.13), phase: r() * TAU },
    { k: 2, amp: between(r, 0.17, 0.25), phase: r() * TAU },
    { k: 3, amp: between(r, 0.08, 0.14), phase: r() * TAU },
    { k: 5, amp: between(r, 0.05, 0.09), phase: r() * TAU },
    { k: 9, amp: between(r, 0.02, 0.045), phase: r() * TAU },
    { k: 17, amp: style === "highland" ? 0.025 : 0.012, phase: r() * TAU },
  ];
  const out: number[] = [];
  for (let i = 0; i < COAST_SAMPLES; i++) {
    const a = (i / COAST_SAMPLES) * TAU;
    let rad = 1;
    for (const w of waves) rad += w.amp * Math.sin(w.k * a + w.phase);
    out.push(rad);
  }
  return out;
}

/** Coast radius at an arbitrary angle, linearly interpolated between samples. */
export function coastAt(map: IslandMap, angle: number): number {
  const t = ((angle % TAU) + TAU) / TAU * COAST_SAMPLES;
  const i = Math.floor(t);
  const f = t - i;
  const a = map.coast[i % COAST_SAMPLES];
  const b = map.coast[(i + 1) % COAST_SAMPLES];
  return a + (b - a) * f;
}

/** How far inside the coast a point sits: 1 at the centre, 0 at the water. */
export function inland(map: IslandMap, x: number, y: number): number {
  const rr = Math.hypot(x, y);
  if (rr < 1e-6) return 1;
  return 1 - rr / coastAt(map, Math.atan2(y, x));
}

/* ------------------------------------------------------------------ relief */

/**
 * Value noise. Smooth gaussian hills alone give a relief map that looks like fog —
 * the eye reads terrain from ridges and gullies, and those come from noise at a scale
 * finer than the landform itself.
 */
function noise2(seed: number, x: number, y: number): number {
  const hash = (i: number, j: number) => {
    let n = (i * 374761393 + j * 668265263 + seed * 1442695040888963407) | 0;
    n = (n ^ (n >> 13)) * 1274126177;
    return ((n ^ (n >> 16)) >>> 0) / 4294967296;
  };
  const i = Math.floor(x);
  const j = Math.floor(y);
  const fx = x - i;
  const fy = y - j;
  // Smoothstep the interpolation or the grid shows through as diamonds.
  const sx = fx * fx * (3 - 2 * fx);
  const sy = fy * fy * (3 - 2 * fy);
  const a = lerpN(hash(i, j), hash(i + 1, j), sx);
  const b = lerpN(hash(i, j + 1), hash(i + 1, j + 1), sx);
  return lerpN(a, b, sy);
}

const lerpN = (a: number, b: number, t: number) => a + (b - a) * t;

/** Elevation, 0 at sea level to ~1 at the highest summit. */
export function elevation(map: IslandMap, x: number, y: number): number {
  let h = 0;
  for (const p of map.peaks) {
    const d2 = (x - p.x) ** 2 + (y - p.y) ** 2;
    h += p.h * Math.exp(-d2 / (p.r * p.r));
  }
  // Three octaves of ridging over the landform. Uplands break up hard; the plains keep
  // a third of the amplitude, because dead-flat ground shades to a single flat colour
  // and reads as paper rather than as land.
  const s = map.noiseSeed;
  const ridge =
    noise2(s, x * 3.1, y * 3.1) * 0.5 +
    noise2(s + 1, x * 7.3, y * 7.3) * 0.3 +
    noise2(s + 2, x * 15.7, y * 15.7) * 0.2;
  h += (ridge - 0.5) * 0.3 * (0.34 + 0.66 * Math.min(1, h * 2.2));

  // Nothing is high at the waterline, however close a summit happens to be.
  const shore = Math.min(1, Math.max(0, inland(map, x, y)) * 3.2);
  return Math.max(0, Math.min(1, h)) * shore;
}

function makePeaks(r: Rand, style: IslandStyle): Peak[] {
  // One very broad, very low swell under everything, so the interior is never a flat
  // plane. Without it the outer two thirds of an island — which is most of its area —
  // sat at exactly zero and shaded to one dead colour.
  const peaks: Peak[] = [
    {
      x: between(r, -0.15, 0.15),
      y: between(r, -0.15, 0.15),
      h: style === "highland" ? 0.2 : 0.13,
      r: 0.95,
    },
  ];
  if (style === "highland") {
    // A spine, not a scatter: one ridge line with summits strung along it. Tight radii
    // so the ridge stays a ridge instead of merging into one dome over the whole island.
    const angle = between(r, -0.7, 0.7);
    const count = 4;
    for (let i = 0; i < count; i++) {
      const t = (i / (count - 1) - 0.5) * 1.2;
      peaks.push({
        x: Math.cos(angle) * t + between(r, -0.1, 0.1),
        y: Math.sin(angle) * t + between(r, -0.1, 0.1),
        // Summed with the broad swell underneath, so kept well clear of 1 — a ridge
        // that saturates the top of the ramp renders as a lens flare, not a mountain.
        h: between(r, 0.3, 0.46),
        r: between(r, 0.22, 0.32),
      });
    }
  } else {
    // Low hills set back from a broad coastal plain. Summed gaussians overlap, so the
    // individual heights have to stay well under 1 or the middle of the country turns
    // into an alpine icecap on what is meant to be a river delta.
    for (let i = 0; i < 3; i++) {
      const a = r() * TAU;
      const d = between(r, 0.16, 0.46);
      peaks.push({
        x: Math.cos(a) * d,
        y: Math.sin(a) * d * 0.8,
        h: between(r, 0.16, 0.3),
        r: between(r, 0.26, 0.38),
      });
    }
  }
  return peaks;
}

/* ------------------------------------------------------------------ rivers */

/**
 * Walk downhill from a summit until the water. Sampling eight directions and taking
 * the steepest descent, with a little jitter and momentum, gives the meander you get
 * from a real flow field without the cost of one.
 */
function traceRiver(
  map: IslandMap, r: Rand, from: Peak
): Array<[number, number]> {
  const path: Array<[number, number]> = [];
  let x = from.x + between(r, -0.05, 0.05);
  let y = from.y + between(r, -0.05, 0.05);
  let vx = 0;
  let vy = 0;

  for (let step = 0; step < 90; step++) {
    path.push([x, y]);
    if (inland(map, x, y) <= 0.005) break;

    let bx = 0;
    let by = 0;
    let best = Infinity;
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * TAU;
      const px = x + Math.cos(a) * 0.05;
      const py = y + Math.sin(a) * 0.05;
      // Falling to the sea counts as the steepest descent there is.
      const h = inland(map, px, py) <= 0 ? -1 : elevation(map, px, py);
      if (h < best) {
        best = h;
        bx = Math.cos(a);
        by = Math.sin(a);
      }
    }
    // Momentum keeps it from oscillating between two equally low neighbours.
    vx = vx * 0.55 + bx * 0.45 + between(r, -0.16, 0.16);
    vy = vy * 0.55 + by * 0.45 + between(r, -0.16, 0.16);
    const len = Math.hypot(vx, vy) || 1;
    x += (vx / len) * 0.045;
    y += (vy / len) * 0.045;
  }
  return path;
}

/* ------------------------------------------------------------------ settlement */

const CAPITALS: Record<IslandStyle, string[]> = {
  delta: ["Vaelport", "Rensmouth", "Aster Quay"],
  highland: ["Korsavgrad", "Duren", "Volsk"],
};
const TOWNS: Record<IslandStyle, string[]> = {
  delta: ["Halloway", "Brightsea", "Netherfield", "Colm", "Waverley"],
  highland: ["Tarn", "Ostrig", "Belmark", "Zhelen", "Kova"],
};

/** A block of buildings, denser and taller towards the middle. */
function makeBuildings(r: Rand, count: number, spread: number): Building[] {
  const out: Building[] = [];
  for (let i = 0; i < count; i++) {
    const a = r() * TAU;
    // sqrt keeps the sample uniform over the disc instead of clumping at the centre.
    const d = Math.sqrt(r()) * spread;
    const dx = Math.cos(a) * d;
    const dy = Math.sin(a) * d * 0.7;
    const core = 1 - d / spread;
    out.push({
      dx,
      dy,
      w: between(r, 0.011, 0.026),
      h: between(r, 0.009, 0.02),
      tall: core * between(r, 0.4, 1),
    });
  }
  return out;
}

/** Somewhere on land, low enough to build on, and not on top of something else. */
function findSpot(
  map: IslandMap, r: Rand, taken: Array<[number, number]>,
  opts: { maxHeight: number; minInland: number; maxInland: number; clear: number }
): [number, number] {
  let best: [number, number] = [0, 0];
  let bestScore = -Infinity;
  for (let i = 0; i < 400; i++) {
    const a = r() * TAU;
    const d = Math.sqrt(r()) * 1.1;
    const x = Math.cos(a) * d;
    const y = Math.sin(a) * d;
    const deep = inland(map, x, y);
    if (deep < opts.minInland || deep > opts.maxInland) continue;
    const h = elevation(map, x, y);
    if (h > opts.maxHeight) continue;
    const near = taken.reduce(
      (m, [tx, ty]) => Math.min(m, Math.hypot(tx - x, ty - y)), Infinity
    );
    if (near < opts.clear) continue;
    // Prefer flat ground with room around it.
    const score = near - h * 2;
    if (score > bestScore) {
      bestScore = score;
      best = [x, y];
    }
  }
  return best;
}

/* ------------------------------------------------------------------ roads */

/** A gently bowed line between two places, sampled so it can be drawn as a curve. */
function road(
  r: Rand, from: [number, number], to: [number, number]
): Array<[number, number]> {
  const [ax, ay] = from;
  const [bx, by] = to;
  const mx = (ax + bx) / 2;
  const my = (ay + by) / 2;
  const nx = -(by - ay);
  const ny = bx - ax;
  const bend = between(r, -0.16, 0.16);
  const cx = mx + nx * bend;
  const cy = my + ny * bend;

  const out: Array<[number, number]> = [];
  for (let i = 0; i <= 14; i++) {
    const t = i / 14;
    const u = 1 - t;
    out.push([
      u * u * ax + 2 * u * t * cx + t * t * bx,
      u * u * ay + 2 * u * t * cy + t * t * by,
    ]);
  }
  return out;
}

/* ------------------------------------------------------------------ assembly */

export function buildIsland(seed: number, style: IslandStyle): IslandMap {
  const r = rng(seed);
  const coast = makeCoast(r, style);
  const peaks = makePeaks(r, style);
  const map: IslandMap = {
    coast, peaks, noiseSeed: seed,
    rivers: [], roads: [], forests: [], farms: [], sites: [], style,
  };

  // ---------------------------------------------------------------- water
  const sources = [...peaks].sort((a, b) => b.h - a.h).slice(0, style === "highland" ? 3 : 2);
  for (const peak of sources) {
    const path = traceRiver(map, r, peak);
    if (path.length > 8) map.rivers.push(path);
  }

  // ---------------------------------------------------------------- places
  const taken: Array<[number, number]> = [];
  const place = (
    kind: SiteKind, role: SiteRole, name: string,
    opts: { maxHeight: number; minInland: number; maxInland: number; clear: number },
    size: number, buildings = 0, spread = 0
  ): Site => {
    const [x, y] = findSpot(map, r, taken, opts);
    taken.push([x, y]);
    const site: Site = {
      kind, role, name, x, y, size, angle: r() * TAU,
      buildings: buildings ? makeBuildings(r, buildings, spread) : [],
    };
    map.sites.push(site);
    return site;
  };

  // The capital sits near the water, the way capitals do. A delta nation sprawls;
  // a highland one is compact because there is nowhere flat to sprawl into.
  const wide = style === "delta";
  const capital = place(
    "capital", "civilian", CAPITALS[style][Math.floor(r() * CAPITALS[style].length)],
    { maxHeight: 0.3, minInland: 0.08, maxInland: 0.45, clear: 0 },
    wide ? 0.2 : 0.16, wide ? 54 : 44, wide ? 0.17 : 0.13
  );

  const townNames = [...TOWNS[style]].sort(() => r() - 0.5);
  const towns: Site[] = [];
  for (let i = 0; i < (wide ? 3 : 2); i++) {
    towns.push(place(
      "town", "civilian", townNames[i],
      { maxHeight: 0.45, minInland: 0.06, maxInland: 0.6, clear: 0.42 },
      0.1, 18, 0.075
    ));
  }

  // Runways need flat ground and a lot of it.
  const airbase = place(
    "airbase", "military", "airfield",
    { maxHeight: 0.18, minInland: 0.12, maxInland: 0.55, clear: 0.34 },
    wide ? 0.19 : 0.15
  );
  // Aligned along the prevailing wind rather than at random — parallel runways read
  // as an airport, a runway at a jaunty angle reads as a scratch.
  airbase.angle = between(r, -0.35, 0.35);

  // A harbour has to actually touch the sea.
  const port = place(
    "port", "military", "naval yard",
    { maxHeight: 0.14, minInland: 0.005, maxInland: 0.1, clear: 0.3 },
    wide ? 0.13 : 0.16
  );
  port.angle = Math.atan2(port.y, port.x);

  place(
    "plant", "infrastructure", "power station",
    { maxHeight: 0.3, minInland: 0.08, maxInland: 0.5, clear: 0.28 },
    0.09
  );
  place(
    "plant", "infrastructure", "refinery",
    { maxHeight: 0.35, minInland: 0.08, maxInland: 0.55, clear: 0.28 },
    0.08
  );

  // Air defence batteries. They never take a hit; they are where interceptors come
  // from, so the player can see *why* a strike was blunted.
  for (let i = 0; i < 3; i++) {
    place(
      "battery", "military", "battery",
      { maxHeight: 0.6, minInland: 0.05, maxInland: 0.7, clear: 0.26 },
      0.045
    );
  }

  // ---------------------------------------------------------------- roads
  for (const site of map.sites) {
    if (site === capital || site.kind === "battery") continue;
    map.roads.push(road(r, [capital.x, capital.y], [site.x, site.y]));
  }
  // One coastal link between towns so the network is not a pure star.
  if (towns.length >= 2) {
    map.roads.push(road(r, [towns[0].x, towns[0].y], [towns[1].x, towns[1].y]));
  }

  // ---------------------------------------------------------------- cover
  const clearOf = (x: number, y: number, dist: number) =>
    map.sites.every((s) => Math.hypot(s.x - x, s.y - y) > dist);

  const clumps = style === "highland" ? 7 : 4;
  for (let c = 0; c < clumps; c++) {
    const [x, y] = findSpot(map, r, [], {
      maxHeight: 0.85, minInland: 0.06, maxInland: 0.8, clear: 0,
    });
    if (!clearOf(x, y, 0.13)) continue;
    const trees: Array<[number, number, number, number]> = [];
    const count = Math.floor(between(r, 16, 34));
    const spread = between(r, 0.1, 0.2);
    for (let i = 0; i < count; i++) {
      const a = r() * TAU;
      const d = Math.sqrt(r()) * spread;
      const tx = x + Math.cos(a) * d;
      const ty = y + Math.sin(a) * d * 0.75;
      if (inland(map, tx, ty) <= 0.02) continue;
      trees.push([tx, ty, between(r, 0.012, 0.024), Math.floor(r() * 3)]);
    }
    if (trees.length > 6) map.forests.push({ x, y, trees });
  }

  // Farmland: low, flat, and only where a delta nation has room for it.
  const fields = style === "delta" ? 26 : 11;
  for (let i = 0; i < fields; i++) {
    const [x, y] = findSpot(map, r, [], {
      maxHeight: 0.22, minInland: 0.05, maxInland: 0.7, clear: 0,
    });
    if (!clearOf(x, y, 0.1)) continue;
    map.farms.push({
      x, y,
      w: between(r, 0.05, 0.11),
      h: between(r, 0.035, 0.075),
      angle: between(r, -0.5, 0.5),
      tone: r(),
    });
  }

  return map;
}

/* ------------------------------------------------------------------ targeting */

/**
 * Which place an incoming round is actually aimed at.
 *
 * Preferring the least-damaged matching site means a campaign works its way across a
 * country rather than cratering the same block eight times, and it makes "integrity"
 * legible: by the end of a long war every town on the map is burning.
 */
export function pickTarget(
  map: IslandMap, role: SiteRole, damage: Map<string, number>
): Site {
  const matches = map.sites.filter((s) => s.role === role && s.kind !== "battery");
  const pool = matches.length ? matches : map.sites.filter((s) => s.kind !== "battery");
  let best = pool[0];
  let least = Infinity;
  for (const site of pool) {
    const hurt = damage.get(siteKey(site)) ?? 0;
    if (hurt < least) {
      least = hurt;
      best = site;
    }
  }
  return best;
}

/** Where a sortie of this kind leaves from. Ordnance should come out of somewhere. */
export function launchSite(map: IslandMap, domain: string): Site {
  const want: SiteKind = domain === "naval" ? "port" : domain === "cyber" ? "capital" : "airbase";
  return map.sites.find((s) => s.kind === want) ?? map.sites[0];
}

export const siteKey = (s: Site) => `${s.kind}:${s.name}:${s.x.toFixed(3)}`;

export const batteries = (map: IslandMap) => map.sites.filter((s) => s.kind === "battery");
