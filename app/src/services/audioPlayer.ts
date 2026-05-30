import * as audioLevel from "./audioLevel";

export class StreamingAudioPlayer {
  private ms: MediaSource;
  private sb: SourceBuffer | null = null;
  private audio: HTMLAudioElement;
  private objectUrl: string;
  private queue: ArrayBuffer[] = [];
  private flushing = false;
  private ended = false;
  private aborted = false;
  private onEnd?: () => void;

  constructor(onEnd?: () => void) {
    this.onEnd = onEnd;
    this.ms = new MediaSource();
    this.objectUrl = URL.createObjectURL(this.ms);
    this.audio = new Audio(this.objectUrl);
    audioLevel.attachElement(this.audio); // pulse the orb with the spoken reply

    this.ms.addEventListener("sourceopen", () => {
      if (this.aborted) return;
      this.sb = this.ms.addSourceBuffer("audio/mpeg");
      // No persistent updateend listener here — only _flush's { once: true } listener runs
      if (this.queue.length > 0) this._flush();
    });

    this.audio.addEventListener("ended", () => {
      if (this.ended && !this.aborted) {
        this.onEnd?.();
        this._cleanup();
      }
    });

    this.audio.play().catch((e) => console.error("[AudioPlayer] autoplay blocked:", e));
  }

  append(b64: string) {
    if (this.aborted) return;
    const buf = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)).buffer;
    this.queue.push(buf);
    this._flush();
  }

  done() {
    if (this.aborted) return;
    this.ended = true;
    if (!this.flushing && this.queue.length === 0 && this.sb) {
      try { this.ms.endOfStream(); } catch { /* already closed */ }
    }
  }

  abort() {
    this.aborted = true;
    this.queue = [];
    this.audio.pause();
    try { this.ms.endOfStream("network"); } catch { /* may already be closed */ }
    this._cleanup();
  }

  private _flush() {
    if (this.aborted || this.flushing || !this.sb || this.queue.length === 0) return;
    this.flushing = true;
    const chunk = this.queue.shift()!;
    try {
      this.sb.appendBuffer(chunk);
    } catch (e) {
      console.error("[AudioPlayer] appendBuffer failed:", e);
      this.flushing = false;
      return;
    }
    this.sb.addEventListener("updateend", () => {
      this.flushing = false;
      if (this.aborted) return;
      if (this.queue.length > 0) {
        this._flush();
      } else if (this.ended) {
        try { this.ms.endOfStream(); } catch { /* already closed */ }
      }
    }, { once: true });
  }

  private _cleanup() {
    URL.revokeObjectURL(this.objectUrl);
  }
}
