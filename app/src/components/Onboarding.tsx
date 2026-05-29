import { useState, ReactNode } from "react";

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
      <span className="onboarding-orb__glow" />
      <span className="onboarding-orb__core">
        <span className="onboarding-orb__shimmer" />
      </span>
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
    body: "Press these keys from any app to start speaking. Press them again to send. You can also click the floating orb.",
    visual: <HotkeyVisual />,
  },
  {
    title: "Your pinned Recall chat",
    body: "The Recall chat stays pinned at the top of your sidebar. Morning briefs, email triage, and follow-ups arrive there automatically, no prompting needed.",
    visual: <PinnedChatVisual />,
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
