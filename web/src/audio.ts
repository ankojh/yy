import type { GameEvent, Side } from "./types";

type MessageAudio = {
  effects: boolean;
  speak: boolean;
  onSpeechStart?: () => void;
  onSpeechEnd?: () => void;
  onSpeechUnavailable?: () => void;
};

const SOUND_PREFERENCE = "yudhyantra:sound";
const MUSIC_LEVEL = 0.78;
const DUCKED_MUSIC_LEVEL = 0.22;
const WEAPON_DUCKED_MUSIC_LEVEL = 0.48;
const EFFECTS_LEVEL = 0.9;
// Procedural cues peak well below full scale internally; this bus brings them clearly
// above the score while their actual output remains below the full-volume commander.
const WEAPON_MIX_LEVEL = 1.15;
const SPEECH_RATE = 1.25;
const MAX_SPEECH_CACHE = 48;

type SpeechLine = {
  side: Side;
  name: string;
  text: string;
  onStart: () => void;
  onEnd: () => void;
  onUnavailable: () => void;
};

const IMPACT_DELAY: Record<string, number> = {
  drone_swarm: 2600,
  cruise_missile: 1500,
  naval_barrage: 2000,
  cyber_strike: 1700,
  nuke: 3200,
};

/** Recorded score, neural commander speech, and lightweight weapon cues. */
class WarAudio {
  private enabled = this.readPreference();
  private active = false;
  private unlocked = false;
  private focused = document.hasFocus();
  private context: AudioContext | null = null;
  private effects: GainNode | null = null;
  private weaponMix: GainNode | null = null;
  private button: HTMLButtonElement | null = null;
  private music: HTMLAudioElement | null = null;
  private speechDucked = false;
  private weaponDucked = false;
  private weaponDuckTimer: number | null = null;
  private speech: HTMLAudioElement | null = null;
  private speechAbort: AbortController | null = null;
  private speechGeneration = 0;
  private speechQueue: SpeechLine[] = [];
  private currentSpeechLine: SpeechLine | null = null;
  private speechRunning = false;
  private speechFinish: (() => void) | null = null;
  private speechEndpoint = "";
  private speechCache = new Map<string, string>();
  private effectTimers = new Set<number>();

  init(button: HTMLButtonElement, apiOrigin: string) {
    this.button = button;
    this.speechEndpoint = `${apiOrigin}/speech`;
    this.music = new Audio("/audio/imminent-threat.ogg");
    this.music.loop = true;
    this.music.preload = "auto";
    this.music.volume = MUSIC_LEVEL;
    this.renderButton();

    button.onclick = () => {
      if (this.enabled && !this.unlocked) {
        void this.unlock();
        return;
      }
      const enabled = !this.enabled;
      this.setEnabled(enabled);
      if (enabled) void this.unlock();
    };

    // A recording still follows autoplay policy. Any ordinary gesture can unlock it;
    // the sound button owns its first click so that click starts, rather than mutes.
    const unlock = (event: Event) => {
      if (event.target !== button) void this.unlock();
    };
    window.addEventListener("pointerdown", unlock, { once: true, capture: true });
    window.addEventListener("keydown", unlock, { once: true, capture: true });

    window.addEventListener("focus", () => {
      this.focused = true;
      this.refreshPageAudio();
    });
    window.addEventListener("blur", () => {
      this.focused = false;
      this.refreshPageAudio();
    });
    document.addEventListener("visibilitychange", () => this.refreshPageAudio());
  }

  reset() {
    this.active = false;
    this.cancelSpeech();
    this.cancelEffects();
    if (this.music) this.music.currentTime = 0;
    this.refreshMusic();
  }

  setWarActive(active: boolean) {
    this.active = active;
    if (!active) {
      this.cancelSpeech();
      this.cancelEffects();
    }
    this.refreshMusic();
  }

  handleMessage(event: GameEvent, options: MessageAudio): boolean {
    if (!this.enabled || !this.unlocked || !this.pageAudible()) return false;
    const payload = event.payload;
    let effectsStarted = false;
    const startEffects = () => {
      if (effectsStarted || !options.effects || payload.tool !== "strike") return;
      effectsStarted = true;
      const weapon = String(payload.args?.weapon ?? "drone_swarm");
      const salvo = Number(payload.shot?.salvo ?? 1);
      const stopped = Number(payload.shot?.stopped ?? 0);
      this.weaponLaunch(weapon);
      this.scheduleEffect(IMPACT_DELAY[weapon] ?? 1800, () => {
        if (salvo > 0 && stopped >= salvo) this.weaponIntercepted(weapon);
        else this.weaponImpact(weapon);
      });
    };
    const text = String(payload.text ?? "").trim();
    if (options.speak && text) {
      return this.enqueueSpeech(
        payload.side as Side,
        String(payload.name ?? "Commander"),
        text,
        {
          onStart: () => {
            startEffects();
            options.onSpeechStart?.();
          },
          onEnd: options.onSpeechEnd ?? (() => undefined),
          onUnavailable: () => {
            startEffects();
            options.onSpeechUnavailable?.();
          },
        },
      );
    }
    startEffects();
    return false;
  }

  private readPreference(): boolean {
    try {
      return localStorage.getItem(SOUND_PREFERENCE) !== "off";
    } catch {
      return true;
    }
  }

  private setEnabled(enabled: boolean) {
    this.enabled = enabled;
    try {
      localStorage.setItem(SOUND_PREFERENCE, enabled ? "on" : "off");
    } catch {
      // Private browsing can reject storage; audio still works for this page lifetime.
    }
    if (!enabled) {
      this.cancelSpeech();
      this.cancelEffects();
    }
    this.renderButton();
    this.refreshMusic();
  }

  private renderButton() {
    if (!this.button) return;
    const waiting = this.enabled && !this.unlocked;
    this.button.textContent = waiting ? "start sound" : this.enabled ? "sound on" : "sound off";
    this.button.classList.toggle("on", this.enabled);
    this.button.setAttribute("aria-pressed", String(this.enabled));
    this.button.title = waiting
      ? "start music, weapon effects, and island voices"
      : this.enabled
      ? "mute music, weapon effects, and island voices"
      : "enable music, weapon effects, and island voices";
  }

  private async unlock() {
    if (!this.enabled) return;
    if (!this.context) {
      this.context = new AudioContext();
      this.effects = this.context.createGain();
      this.effects.gain.value = this.pageAudible() ? EFFECTS_LEVEL : 0.0001;
      this.effects.connect(this.context.destination);
      this.weaponMix = this.context.createGain();
      this.weaponMix.gain.value = WEAPON_MIX_LEVEL;
      this.weaponMix.connect(this.effects);
    }

    // Start the HTML audio synchronously inside the gesture; awaiting resume first can
    // consume Safari's user-activation allowance.
    this.unlocked = true;
    this.renderButton();
    this.refreshMusic();
    if (this.context.state === "suspended") await this.context.resume();
    this.unlocked = this.context.state === "running";
    this.renderButton();
    this.refreshMusic();
  }

  private pageAudible() {
    return this.focused && !document.hidden;
  }

  private refreshPageAudio() {
    if (!this.pageAudible()) {
      this.cancelSpeech();
      this.cancelEffects();
    }
    const context = this.context;
    const gain = this.effects?.gain;
    if (context && gain) {
      const now = context.currentTime;
      gain.cancelScheduledValues(now);
      gain.setValueAtTime(Math.max(0.0001, gain.value), now);
      gain.exponentialRampToValueAtTime(
        this.pageAudible() ? EFFECTS_LEVEL : 0.0001,
        now + (this.pageAudible() ? 0.12 : 0.035),
      );
    }
    this.refreshMusic();
  }

  private refreshMusic() {
    const music = this.music;
    if (!music) return;
    const shouldPlay = this.enabled && this.active && this.unlocked && this.pageAudible();
    if (!shouldPlay) {
      music.pause();
      return;
    }
    void music.play().catch(() => {
      this.unlocked = false;
      this.renderButton();
    });
  }

  private duck(ducked: boolean) {
    this.speechDucked = ducked;
    this.applyMusicMix();
  }

  private accentWeapon(duration = 950) {
    this.weaponDucked = true;
    if (this.weaponDuckTimer !== null) window.clearTimeout(this.weaponDuckTimer);
    this.weaponDuckTimer = window.setTimeout(() => {
      this.weaponDuckTimer = null;
      this.weaponDucked = false;
      this.applyMusicMix();
    }, duration);
    this.applyMusicMix();
  }

  private applyMusicMix() {
    if (!this.music) return;
    this.music.volume = this.speechDucked
      ? DUCKED_MUSIC_LEVEL
      : this.weaponDucked
        ? WEAPON_DUCKED_MUSIC_LEVEL
        : MUSIC_LEVEL;
  }

  private enqueueSpeech(
    side: Side,
    name: string,
    text: string,
    hooks: Pick<SpeechLine, "onStart" | "onEnd" | "onUnavailable">,
  ): boolean {
    if (!text) return false;
    this.speechQueue.push({ side, name, text, ...hooks });
    void this.drainSpeechQueue();
    return true;
  }

  private async drainSpeechQueue() {
    if (this.speechRunning) return;
    this.speechRunning = true;
    const generation = this.speechGeneration;
    try {
      while (
        generation === this.speechGeneration
        && this.enabled && this.unlocked && this.pageAudible()
      ) {
        const line = this.speechQueue.shift();
        if (!line) break;
        this.currentSpeechLine = line;
        await this.speak(line, generation);
        if (this.currentSpeechLine === line) this.currentSpeechLine = null;
      }
    } finally {
      this.speechRunning = false;
      if (generation === this.speechGeneration) this.duck(false);
      if (this.speechQueue.length && this.enabled && this.unlocked && this.pageAudible()) {
        void this.drainSpeechQueue();
      }
    }
  }

  private async speak(line: SpeechLine, generation: number) {
    const { side, name, text } = line;
    const key = `${side}\0${name}\0${text}`;

    try {
      let url = this.speechCache.get(key);
      if (!url) {
        const abort = new AbortController();
        this.speechAbort = abort;
        const response = await fetch(this.speechEndpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ side, name, text }),
          signal: abort.signal,
        });
        if (!response.ok) throw new Error(`speech unavailable: ${response.status}`);
        const blob = await response.blob();
        if (generation !== this.speechGeneration) return;
        url = URL.createObjectURL(blob);
        this.cacheSpeech(key, url);
      }
      if (generation !== this.speechGeneration || !this.pageAudible()) return;

      const audio = new Audio(url);
      this.speech = audio;
      audio.volume = 1;
      audio.defaultPlaybackRate = SPEECH_RATE;
      audio.playbackRate = SPEECH_RATE;
      audio.preservesPitch = true;
      let started = false;
      audio.onplay = () => {
        if (generation === this.speechGeneration) {
          started = true;
          line.onStart();
          this.duck(true);
          this.commandCue(side);
        }
      };
      await new Promise<void>((resolve) => {
        let done = false;
        const finished = (unavailable = false) => {
          if (done) return;
          done = true;
          if (this.speech === audio) {
            this.speech = null;
            this.speechFinish = null;
          }
          if (started) line.onEnd();
          else if (unavailable) line.onUnavailable();
          resolve();
        };
        this.speechFinish = () => finished(false);
        audio.onended = () => finished(false);
        audio.onerror = () => finished(true);
        void audio.play().catch(() => finished(true));
      });
      if (generation === this.speechGeneration) this.duck(false);
    } catch (error) {
      if ((error as DOMException).name === "AbortError") return;
      // An unavailable neural provider is intentionally silent. The platform speech
      // fallback was removed because its robotic output is worse than keeping the text.
      if (generation === this.speechGeneration) line.onUnavailable();
    } finally {
      if (generation === this.speechGeneration) this.speechAbort = null;
    }
  }

  private cacheSpeech(key: string, url: string) {
    this.speechCache.set(key, url);
    while (this.speechCache.size > MAX_SPEECH_CACHE) {
      const oldest = this.speechCache.keys().next().value as string | undefined;
      if (!oldest) break;
      const stale = this.speechCache.get(oldest);
      if (stale) URL.revokeObjectURL(stale);
      this.speechCache.delete(oldest);
    }
  }

  private cancelSpeech() {
    this.speechGeneration += 1;
    for (const line of this.speechQueue) line.onEnd();
    this.speechQueue = [];
    this.currentSpeechLine?.onEnd();
    this.currentSpeechLine = null;
    this.speechAbort?.abort();
    this.speechAbort = null;
    if (this.speech) {
      this.speech.pause();
      this.speech.currentTime = 0;
      this.speech = null;
    }
    this.speechFinish?.();
    this.speechFinish = null;
    this.duck(false);
  }

  private commandCue(side: Side) {
    this.noise(0.09, 0.009, 1250, 0, "cue");
    this.tone("square", side === "west" ? 155 : 132, 92, 0.11, 0.009, 0, "cue");
  }

  /** The report at the firing island: every system has a distinct launch signature. */
  private weaponLaunch(weapon: string) {
    this.accentWeapon(1000);
    switch (weapon) {
      case "drone_swarm":
        this.tone("sawtooth", 118, 186, 0.72, 0.11);
        this.noise(0.8, 0.07, 1450);
        this.tone("square", 260, 210, 0.18, 0.035, 0.18);
        break;
      case "cruise_missile":
        this.noise(0.92, 0.12, 1900);
        this.tone("sawtooth", 240, 72, 0.9, 0.095);
        this.boom(0, 0.09);
        break;
      case "naval_barrage":
        this.boom(0, 0.13);
        this.boom(0.28, 0.105);
        this.noise(0.7, 0.08, 720, 0.06);
        break;
      case "cyber_strike":
        this.tone("square", 1260, 310, 0.12, 0.07);
        this.tone("square", 880, 190, 0.15, 0.065, 0.16);
        this.tone("square", 620, 95, 0.2, 0.055, 0.35);
        break;
      case "nuke":
        this.tone("sawtooth", 74, 43, 1.5, 0.11);
        this.noise(1.4, 0.095, 390, 0.08);
        this.tone("square", 410, 360, 0.22, 0.05, 0.2);
        break;
      default:
        this.tone("sawtooth", 145, 88, 0.75, 0.08);
        this.noise(0.55, 0.07, 1300);
    }
  }

  /** The receiving island: impacts follow the same flight times as the canvas rounds. */
  private weaponImpact(weapon: string) {
    this.accentWeapon(weapon === "nuke" ? 2400 : 1250);
    switch (weapon) {
      case "drone_swarm":
        this.boom(0, 0.085);
        this.boom(0.16, 0.07);
        this.boom(0.34, 0.055);
        this.noise(0.65, 0.08, 900);
        break;
      case "cruise_missile":
        this.boom(0, 0.17);
        this.noise(1.1, 0.13, 560, 0.02);
        this.tone("sine", 68, 31, 1.2, 0.12);
        break;
      case "naval_barrage":
        this.boom(0, 0.12);
        this.boom(0.3, 0.11);
        this.boom(0.62, 0.1);
        this.boom(0.92, 0.075);
        break;
      case "cyber_strike":
        this.noise(0.5, 0.09, 3200);
        this.tone("square", 220, 48, 0.55, 0.085);
        this.tone("sine", 54, 28, 0.85, 0.07, 0.18);
        break;
      case "nuke":
        this.boom(0, 0.24);
        this.noise(2.2, 0.19, 310, 0.03);
        this.tone("sine", 52, 22, 2.4, 0.18);
        this.boom(0.38, 0.13);
        break;
      default:
        this.boom(0, 0.11);
        this.noise(0.7, 0.08, 650);
    }
  }

  private weaponIntercepted(weapon: string) {
    this.accentWeapon(850);
    const nuclear = weapon === "nuke";
    this.noise(nuclear ? 0.9 : 0.38, nuclear ? 0.12 : 0.075, nuclear ? 520 : 2600);
    this.tone("square", nuclear ? 180 : 980, nuclear ? 46 : 210, nuclear ? 0.8 : 0.3, 0.08);
  }

  private scheduleEffect(delay: number, play: () => void) {
    const timer = window.setTimeout(() => {
      this.effectTimers.delete(timer);
      if (this.enabled && this.unlocked && this.pageAudible()) play();
    }, delay);
    this.effectTimers.add(timer);
  }

  private cancelEffects() {
    for (const timer of this.effectTimers) window.clearTimeout(timer);
    this.effectTimers.clear();
    if (this.weaponDuckTimer !== null) window.clearTimeout(this.weaponDuckTimer);
    this.weaponDuckTimer = null;
    this.weaponDucked = false;
    this.applyMusicMix();
  }

  private boom(delay: number, volume: number) {
    this.tone("sine", 92, 34, 0.72, volume, delay);
    this.noise(0.6, volume * 0.62, 420, delay);
  }

  private tone(
    type: OscillatorType,
    startFrequency: number,
    endFrequency: number,
    duration: number,
    volume: number,
    delay = 0,
    route: "weapon" | "cue" = "weapon",
  ) {
    const context = this.context;
    const output = route === "cue" ? this.effects : this.weaponMix;
    if (!context || !output || !this.enabled || !this.pageAudible()) return;
    const start = context.currentTime + delay;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = type;
    oscillator.frequency.setValueAtTime(startFrequency, start);
    oscillator.frequency.exponentialRampToValueAtTime(endFrequency, start + duration);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(volume, start + 0.018);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain).connect(output);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.03);
  }

  private noise(
    duration: number, volume: number, cutoff: number, delay = 0,
    route: "weapon" | "cue" = "weapon",
  ) {
    const context = this.context;
    const output = route === "cue" ? this.effects : this.weaponMix;
    if (!context || !output || !this.enabled || !this.pageAudible()) return;
    const length = Math.ceil(context.sampleRate * duration);
    const buffer = context.createBuffer(1, length, context.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < channel.length; index += 1) {
      channel[index] = Math.random() * 2 - 1;
    }
    const start = context.currentTime + delay;
    const source = context.createBufferSource();
    const filter = context.createBiquadFilter();
    const gain = context.createGain();
    source.buffer = buffer;
    filter.type = "lowpass";
    filter.frequency.value = cutoff;
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(volume, start + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    source.connect(filter).connect(gain).connect(output);
    source.start(start);
  }
}

export const warAudio = new WarAudio();
