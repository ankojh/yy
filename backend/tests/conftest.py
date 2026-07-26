import os
import sys
from pathlib import Path

# Tests never touch the network and never write a transcript. Every pacing knob is
# zeroed — REVEAL alone is ten seconds per declared action in the real thing, which
# would turn a twelve-turn match into four minutes of sleeping.
os.environ.setdefault("MOCK", "1")
os.environ.setdefault("BEAT", "0")
os.environ.setdefault("REVEAL", "0")
os.environ.setdefault("TURN_PAUSE", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
