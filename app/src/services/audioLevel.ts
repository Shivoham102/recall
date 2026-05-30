// Shared audio-level bus + the single page AudioContext.
//
// Both the mic (while recording) and TTS playback publish a 0..1 RMS level
// here; the voice Orb shader reads it (`getLevel`) for amplitude-reactive
// motion. One AudioContext for the whole page, owned here and borrowed by
// both sources — never `new AudioContext()` elsewhere (per-page limit).

let ctx: AudioContext | null = null;
let ctxFailed = false;

let level = 0;

// ── Mic tap ──
let micSource: MediaStreamAudioSourceNode | null = null;
let micAnalyser: AnalyserNode | null = null;
let micData: Uint8Array | null = null;
let micRaf = 0;

// ── TTS (media element) tap ──
interface ElTap {
  source: MediaElementAudioSourceNode;
  analyser: AnalyserNode;
  data: Uint8Array;
}
// createMediaElementSource throws if an element is bound twice (HMR re-mount /
// repeated playAudio), so cache the node per element and reuse it.
const elementTaps = new WeakMap<HTMLMediaElement, ElTap>();
let activeTap: ElTap | null = null;
let elRaf = 0;

function getCtx(): AudioContext | null {
  if (ctx || ctxFailed) return ctx;
  try {
    const Ctor: typeof AudioContext | undefined =
      window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) {
      ctxFailed = true;
      return null;
    }
    ctx = new Ctor();
  } catch {
    ctxFailed = true;
    ctx = null;
  }
  return ctx;
}

/** Resume the shared context. Safe to call from the orb toggle / hotkey path. */
export function resume(): void {
  const c = getCtx();
  if (c && c.state === "suspended") c.resume().catch(() => {});
}

/** Latest amplitude, 0..1. */
export function getLevel(): number {
  return level;
}

/** True while a real source (mic or TTS) is actively publishing. */
export function isLive(): boolean {
  return micRaf !== 0 || elRaf !== 0;
}

function rms(data: Uint8Array): number {
  let sum = 0;
  for (let i = 0; i < data.length; i++) {
    const v = (data[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / data.length);
}

// Speech/voice RMS is small; lift it into a lively 0..1 range.
function shape(r: number): number {
  return Math.min(1, r * 3.2);
}

// ── Mic ──────────────────────────────────────────────────────────────

/** Tap a live mic stream (analyser only — never routed to output, no echo). */
export function attachStream(stream: MediaStream): void {
  const c = getCtx();
  if (!c) return;
  resume();
  detachStream();
  try {
    micSource = c.createMediaStreamSource(stream);
    micAnalyser = c.createAnalyser();
    micAnalyser.fftSize = 512;
    micAnalyser.smoothingTimeConstant = 0.75;
    micSource.connect(micAnalyser);
    micData = new Uint8Array(micAnalyser.fftSize);
    const loop = () => {
      if (!micAnalyser || !micData) {
        micRaf = 0;
        return;
      }
      micAnalyser.getByteTimeDomainData(micData);
      level = shape(rms(micData));
      micRaf = requestAnimationFrame(loop);
    };
    micRaf = requestAnimationFrame(loop);
  } catch {
    detachStream();
  }
}

export function detachStream(): void {
  if (micRaf) cancelAnimationFrame(micRaf);
  micRaf = 0;
  try {
    micSource?.disconnect();
  } catch {
    /* noop */
  }
  try {
    micAnalyser?.disconnect();
  } catch {
    /* noop */
  }
  micSource = null;
  micAnalyser = null;
  micData = null;
  level = 0;
}

// ── TTS (HTMLAudioElement) ───────────────────────────────────────────

/**
 * Tap a playing media element so the orb pulses with the voice.
 *
 * Only taps when the context is already `running` (i.e. a user gesture has
 * activated it). Binding a MediaElementSource while suspended would route the
 * element through the graph and MUTE it — fatal for proactive reminders that
 * play with no gesture. In that case we leave the element untouched and the
 * orb uses its synthesized speech envelope instead.
 */
export function attachElement(el: HTMLMediaElement): void {
  const c = getCtx();
  if (!c) return;
  resume();
  if (c.state !== "running") return; // stay safe — do not mute non-gesture playback
  try {
    let tap = elementTaps.get(el);
    if (!tap) {
      const source = c.createMediaElementSource(el);
      const analyser = c.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.75;
      source.connect(analyser);
      source.connect(c.destination); // keep audio audible
      tap = { source, analyser, data: new Uint8Array(analyser.fftSize) };
      elementTaps.set(el, tap);
    }
    activeTap = tap;
    startElLoop();
    const onDone = () => {
      el.removeEventListener("ended", onDone);
      el.removeEventListener("pause", onDone);
      if (activeTap === tap) stopElLoop();
    };
    el.addEventListener("ended", onDone);
    el.addEventListener("pause", onDone);
  } catch {
    stopElLoop();
  }
}

function startElLoop(): void {
  if (elRaf) return;
  const loop = () => {
    if (!activeTap) {
      elRaf = 0;
      return;
    }
    activeTap.analyser.getByteTimeDomainData(activeTap.data);
    level = shape(rms(activeTap.data));
    elRaf = requestAnimationFrame(loop);
  };
  elRaf = requestAnimationFrame(loop);
}

function stopElLoop(): void {
  if (elRaf) cancelAnimationFrame(elRaf);
  elRaf = 0;
  activeTap = null;
  level = 0;
}
