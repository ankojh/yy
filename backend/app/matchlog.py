"""Per-match transcripts and model accounting on disk.

Three ordinary files per match, plus an optional raw LLM trace when LLM_DEBUG is enabled.
Every line is flushed as it happens so you can `tail -f` a war in progress:

  logs/match-<stamp>.jsonl   every event, for replay or analysis
  logs/match-<stamp>.md      the readable chat transcript
  logs/match-<stamp>-usage.jsonl  token and prompt-cache counters (no prompts)
  logs/match-<stamp>-llm.jsonl  model requests and responses (development only)
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
        self.usage_path = log_dir / f"match-{stamp}-usage.jsonl"
        self.llm_path = log_dir / f"match-{stamp}-llm.jsonl"
        self._jsonl: Optional[TextIO] = self.jsonl_path.open("w", encoding="utf-8")
        self._md: Optional[TextIO] = self.md_path.open("w", encoding="utf-8")
        self._usage: Optional[TextIO] = self.usage_path.open("w", encoding="utf-8")
        # Open lazily: matches run with LLM_DEBUG=0 should not leave an empty raw trace.
        self._llm: Optional[TextIO] = None
        self._say(f"# yudhyantra — {datetime.now():%Y-%m-%d %H:%M:%S}\n")

        # Which rules and which models produced this transcript. Never the API key.
        meta = dict(meta or {})
        self._meta = meta
        if self._jsonl:
            self._jsonl.write(json.dumps({"type": "meta", "turn": 0, "payload": meta}) + "\n")
            self._jsonl.flush()
        if self._usage:
            self._usage.write(
                json.dumps({"type": "meta", "turn": 0, "payload": meta}) + "\n"
            )
            self._usage.flush()
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
            self._say(f"**Council action:** {', '.join(p.get('cards', []))}\n")
            for g in p.get("grievances", []):
                self._say(f"> {g}\n")
        elif ev.type == "injection":
            self._say(f"> _injected:_ {p.get('text', '')}\n")
        elif ev.type in ("support", "support_response"):
            self._say(
                f"> _council support · {p.get('label', '')} · ${p.get('cost', 0)}B:_ "
                f"{p.get('text', '')}\n"
            )
        elif ev.type == "support_request":
            request = p.get("request") or {}
            self._say(f"> _request to the council:_ {request.get('text', '')}\n")
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

    def write_llm_trace(self, payload: Dict[str, Any]) -> None:
        """Append raw model I/O to its own non-replayable diagnostics file."""
        if self._llm is None:
            self._llm = self.llm_path.open("w", encoding="utf-8")
            self._llm.write(
                json.dumps({"type": "meta", "payload": self._meta}, ensure_ascii=False) + "\n"
            )
        record = {
            "type": "llm_trace",
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            **payload,
        }
        self._llm.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._llm.flush()

    def write_llm_usage(self, payload: Dict[str, Any]) -> None:
        """Record cost/cache counters without retaining prompts or model output."""
        if not self._usage:
            return
        allowed = {
            "agent", "direction", "model", "turn", "api", "stateless",
            "chain_position", "chain_reset", "input_tokens", "cached_input_tokens",
            "output_tokens", "total_tokens",
        }
        record = {
            "type": "llm_usage",
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            **{key: value for key, value in payload.items() if key in allowed},
        }
        self._usage.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._usage.flush()

    def close(self) -> None:
        for handle in (self._jsonl, self._md, self._usage, self._llm):
            if handle:
                handle.close()
        self._jsonl = None
        self._md = None
        self._usage = None
        self._llm = None
