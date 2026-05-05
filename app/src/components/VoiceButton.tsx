import { RecorderState } from "../hooks/useRecorder";

interface Props {
  state: RecorderState;
  onStart: () => void;
  onStop: () => void;
}

export function VoiceButton({ state, onStart, onStop }: Props) {
  const label =
    state === "idle" ? "Hold to Speak" :
    state === "recording" ? "Release to Send" :
    "Processing…";

  return (
    <button
      className={`voice-btn voice-btn--${state}`}
      onMouseDown={state === "idle" ? onStart : undefined}
      onMouseUp={state === "recording" ? onStop : undefined}
      onTouchStart={state === "idle" ? (e) => { e.preventDefault(); onStart(); } : undefined}
      onTouchEnd={state === "recording" ? (e) => { e.preventDefault(); onStop(); } : undefined}
      disabled={state === "processing"}
    >
      <span className="voice-btn__icon">
        {state === "recording" ? "⏺" : state === "processing" ? "⏳" : "🎙"}
      </span>
      <span>{label}</span>
    </button>
  );
}
