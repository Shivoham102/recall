import { useState, ReactNode } from "react";
import { Orb, type OrbState } from "./Orb/OrbCanvas";

interface Props {
  onClose: () => void;
}

interface Step {
  title: string;
  body: string;
  visual: ReactNode;
}

function HotkeyVisual() {
  return (
    <div className="onboarding-hotkey">
      <span className="keycap">Ctrl</span>
      <span className="keycap-sep">+</span>
      <span className="keycap">Shift</span>
      <span className="keycap-sep">+</span>
      <span className="keycap">Space</span>
      <span className="onboarding-hotkey__hint">to talk</span>
    </div>
  );
}

function PinnedChatVisual() {
  return (
    <div className="onboarding-mock-sidebar">
      <div className="onboarding-mock-row onboarding-mock-row--pinned">
        <span className="onboarding-mock-row__title">
          <span className="onboarding-mock-row__glyph">◈</span> Recall
        </span>
        <span className="onboarding-mock-row__dot" />
      </div>
      <div className="onboarding-mock-row">
        <span className="onboarding-mock-row__title">Trip planning</span>
      </div>
      <div className="onboarding-mock-row">
        <span className="onboarding-mock-row__title">Standup notes</span>
      </div>
    </div>
  );
}

function IntroVisual() {
  return (
    <div className="onboarding-orb" aria-hidden="true">
      <Orb state="idle" size={72} />
    </div>
  );
}

// The four states the orb cycles through during a normal interaction. "error"
// is an edge state and intentionally omitted here.
const ORB_STATES: { state: OrbState; label: string }[] = [
  { state: "idle", label: "Idle" },
  { state: "recording", label: "Listening" },
  { state: "processing", label: "Thinking" },
  { state: "speaking", label: "Speaking" },
];

function OrbStatesVisual() {
  return (
    <div className="onboarding-orb-states" aria-hidden="true">
      {ORB_STATES.map(({ state, label }) => (
        <div key={state} className="onboarding-orb-state">
          <Orb state={state} size={52} />
          <span className="onboarding-orb-state__label">{label}</span>
        </div>
      ))}
    </div>
  );
}

function CaptureVisual() {
  return (
    <div className="onboarding-capture" aria-hidden="true">
      <div className="onboarding-capture__quote">"I parked on level 3, section B"</div>
      <div className="onboarding-capture__chip">
        <span className="onboarding-capture__check">✓</span> Saved to memory
      </div>
    </div>
  );
}

function TrayVisual() {
  return (
    <div className="onboarding-tray" aria-hidden="true">
      <div className="onboarding-tray__bar">
        <span className="onboarding-tray__app" />
        <span className="onboarding-tray__app" />
        <span className="onboarding-tray__recall">
          <Orb state="idle" size={44} />
        </span>
        <span className="onboarding-tray__clock">9:41</span>
      </div>
    </div>
  );
}

const STEPS: Step[] = [
  {
    title: "Welcome to Recall",
    body: "A voice-first AI assistant that remembers things for you and quietly works in the background, so the right context is always one breath away.",
    visual: <IntroVisual />,
  },
  {
    title: "Talk to Recall anywhere",
    body: "Press these keys from any app to start speaking, and again to send. The orb stays up while Recall thinks and replies. Press again any time to interrupt and ask something new. You can also click the floating orb.",
    visual: <HotkeyVisual />,
  },
  {
    title: "Reading the orb",
    body: "The orb shows what Recall is doing at a glance: calm blue when idle, brighter as it listens to you, swirling violet while it thinks, and a green pulse as it speaks back.",
    visual: <OrbStatesVisual />,
  },
  {
    title: "Just say it, Recall remembers",
    body: "Tell Recall to remember something, like where you parked, a book a friend recommended, or a promise you made, and it's saved. Ask for it later in plain language and it comes right back.",
    visual: <CaptureVisual />,
  },
  {
    title: "Your pinned Recall chat",
    body: "The Recall chat stays pinned at the top of your sidebar. Morning briefs, email triage, and follow-ups arrive there automatically. You can also ask Recall to draft an email reply or triage your calendar and inbox any time.",
    visual: <PinnedChatVisual />,
  },
  {
    title: "Always a breath away",
    body: "Closing the window doesn't quit Recall. It keeps running in your system tray with the orb floating nearby, so you can capture and recall from any app, any time.",
    visual: <TrayVisual />,
  },
];

export function Onboarding({ onClose }: Props) {
  const [step, setStep] = useState(0);
  const isLast = step === STEPS.length - 1;
  const current = STEPS[step];

  return (
    <div className="onboarding-overlay">
      <div className="onboarding-card">
        <div className="onboarding-visual">{current.visual}</div>

        <div className="onboarding-step__title">{current.title}</div>
        <div className="onboarding-step__body">{current.body}</div>

        <div className="onboarding-dots" role="tablist" aria-label="Onboarding progress">
          {STEPS.map((_, i) => (
            <span
              key={i}
              className={`onboarding-dot ${i === step ? "onboarding-dot--active" : ""}`}
            />
          ))}
        </div>

        <div className="onboarding-nav">
          {step > 0 ? (
            <button
              className="onboarding-btn onboarding-btn--ghost"
              onClick={() => setStep((s) => s - 1)}
            >
              Back
            </button>
          ) : (
            <button className="onboarding-btn onboarding-btn--ghost" onClick={onClose}>
              Skip
            </button>
          )}

          <button
            className="onboarding-btn onboarding-btn--primary"
            onClick={() => (isLast ? onClose() : setStep((s) => s + 1))}
          >
            {isLast ? "Get started" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
