export type Side = "west" | "east";

/** Consequences that outlive the turn that created them. */
export interface Effects {
  shield: Record<string, number>;   // domain -> turns of hardened cover left
  morale_buffer: number;            // morale damage absorbed before the public feels it
  blockaded: number;                // turns left under an enemy blockade
  sanctioned: number;               // turns left of sanctions
  spin: number;                     // turns left of a state propaganda campaign
  deficit_turns: number;            // consecutive upkeeps the treasury could not cover
}

export interface Nation {
  side: Side;
  name: string;
  blurb?: string;
  creed?: string;
  morale: number;
  military: number;
  standing: number;
  integrity: number;
  // The war economy.
  gdp: number;
  gdp_base: number;
  budget: number;
  // The two pressures, both per-nation and earned separately.
  intl_pressure: number;
  unrest: number;
  propaganda: number;
  /** Cumulative dead on this nation's soil. A count, not a meter — it has no ceiling. */
  casualties: number;
  defenses: Record<string, number>;
  arsenal: Record<string, number>;
  strike_streak: number;
  effects: Effects;
  /** Landform hints, so each country's island looks like itself every match. */
  terrain?: { seed?: number; style?: string };
}

/** A named place that was destroyed, and what it cost. */
export interface Atrocity {
  turn: number;
  victim: Side;
  attacker: Side;
  place: string;
  dead: number;
  weapon: string;
  protected: boolean;
}

/** An open negotiation. `positions` is article id -> stance per side. */
export interface Talks {
  open: boolean;
  round: number;
  opened_by: Side | null;
  positions: Record<string, Record<string, string>>;
  settled: string[];
  deadlock: number;
  transcript: string[];
}

/** What the two capitals have been arguing about for sixty-one years. */
export interface Article {
  title: string;
  dispute: string;
  core: boolean;
}

export const WEAPONS: Array<{ id: string; label: string; domain: string }> = [
  { id: "drone_swarm", label: "drone swarm", domain: "air" },
  { id: "cruise_missile", label: "cruise missile", domain: "air" },
  { id: "naval_barrage", label: "naval barrage", domain: "naval" },
  { id: "cyber_strike", label: "cyber strike", domain: "cyber" },
  { id: "nuke", label: "nuclear", domain: "air" },
];

export const WEAPON_BY_ID = Object.fromEntries(WEAPONS.map((w) => [w.id, w]));

export interface World {
  turn: number;
  tension: number;
  phase: "briefing" | "conflict" | "over";
  loser: Side | null;
  grievances: string[];
  atrocities: Atrocity[];
  talks: Talks;
  talks_cooldown: number;
  talks_held: number;
  outcome: string | null;
}

export interface GameState {
  world: World;
  west: Nation;
  east: Nation;
}

/** Everything the server says arrives as one of these. The UI is a fold over the stream. */
export interface GameEvent {
  type: string;
  turn: number;
  payload: Record<string, any>;
}
