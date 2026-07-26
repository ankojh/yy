"""Per-match transcripts on disk.

Two files per match, written as it happens and flushed every line so you can `tail -f`
a war in progress:

  logs/match-<stamp>.jsonl   every event, for replay or analysis
  logs/match-<stamp>.md      the readable chat transcript
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TextIO

from .state import Event

WEAPON_LABEL = {
    "drone_swarm": "drone swarm",
    "cruise_missile": "cruise missile",
    "naval_barrage": "naval barrage",
    "cyber_strike": "cyber strike",
    "nuke": "NUCLEAR WARHEAD",
}


class MatchLog:
    def __init__(self, log_dir: Path, meta: Optional[Dict[str, Any]] = None) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        # Batch runs open several matches inside the same millisecond, and two matches
        # sharing a filename means one transcript silently overwrites the other.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        suffix, base = 0, stamp
        while (log_dir / f"match-{stamp}.jsonl").exists():
            suffix += 1
            stamp = f"{base}-{suffix}"
        self.jsonl_path = log_dir / f"match-{stamp}.jsonl"
        self.md_path = log_dir / f"match-{stamp}.md"
        self._jsonl: Optional[TextIO] = self.jsonl_path.open("w", encoding="utf-8")
        self._md: Optional[TextIO] = self.md_path.open("w", encoding="utf-8")
        self._say(f"# yudhyantra — {datetime.now():%Y-%m-%d %H:%M:%S}\n")

        # Which rules and which models produced this transcript. Never the API key.
        meta = dict(meta or {})
        if self._jsonl:
            self._jsonl.write(json.dumps({"type": "meta", "turn": 0, "payload": meta}) + "\n")
            self._jsonl.flush()
        if meta:
            self._say(
                "`" + "` · `".join(f"{k}={v}" for k, v in meta.items() if v is not None) + "`\n"
            )

    def _say(self, line: str) -> None:
        if self._md:
            self._md.write(line + "\n")
            self._md.flush()

    def write(self, ev: Event) -> None:
        if self._jsonl:
            self._jsonl.write(ev.model_dump_json() + "\n")
            self._jsonl.flush()

        p = ev.payload
        if ev.type == "ignition":
            self._say(f"**Casus belli:** {', '.join(p.get('cards', []))}\n")
            for g in p.get("grievances", []):
                self._say(f"> {g}\n")
        elif ev.type == "injection":
            self._say(f"> _injected:_ {p.get('text', '')}\n")
        elif ev.type == "turn_started":
            self._say(f"\n## Turn {p.get('turn')}\n")
        elif ev.type == "decision":
            # Provenance, not deliberation: where the choice came from and what it was
            # allowed to choose from. Never the model's private reasoning.
            bits = [f"[{p.get('source')}"]
            if p.get("model"):
                bits.append(f" {p['model']}")
            bits.append("]")
            if p.get("intent"):
                bits.append(f" intent={p['intent']}")
            if p.get("strike_streak"):
                bits.append(f" fatigue={p['strike_streak']}")
            fx = {k: v for k, v in (p.get("effects") or {}).items() if v}
            if fx:
                bits.append(f" effects={fx}")
            bits.append(f" legal={','.join(p.get('legal') or [])}")
            self._say(f"<!-- {''.join(bits)} -->")
        elif ev.type == "message":
            args = p.get("args") or {}
            bits = [
                WEAPON_LABEL.get(args["weapon"], args["weapon"]) if "weapon" in args else "",
                args.get("target", ""),
                args.get("domain", ""),
            ]
            detail = " → ".join(b for b in bits if b)
            head = f"**{p.get('name')}** — `{p.get('tool')}`"
            self._say(f"{head}{f' ({detail})' if detail else ''}")
            self._say(f'> "{p.get("text", "")}"\n')
        elif ev.type == "ruling":
            verdict = "effective" if p.get("effective") else "INEFFECTIVE"
            mod = p.get("modifier") or 0
            fired = [k for k, v in (p.get("flags") or {}).items() if v and k != "coherent"]
            tail = f" ({', '.join(fired)})" if fired else ""
            self._say(f"  · arbiter: {verdict} {mod:+d}{tail} — {p.get('reason', '')}")
        elif ev.type == "deltas":
            parts = [f"{d['side']}.{d['field']} {d['delta']:+d}" for d in p.get("deltas", [])]
            if parts:
                self._say(f"  · {', '.join(parts)}")
        elif ev.type in ("note", "bulletin"):
            self._say(f"\n_{p.get('text', '')}_\n")
        elif ev.type == "game_over":
            self._say(f"\n## Result\n\n**{p.get('outcome', '')}**\n")

    def close(self) -> None:
        for handle in (self._jsonl, self._md):
            if handle:
                handle.close()
        self._jsonl = None
        self._md = None
