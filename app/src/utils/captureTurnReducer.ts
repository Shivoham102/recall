import type { StreamEvent } from "../services/api";
import type { AgentTurn, CalendarCard, EmailCard, TaskCard } from "../types/agentTurn";

/**
 * The user + assistant turn pair for a single capture (voice/text) exchange.
 * Both `AgentTab` and the orb-window hotkey producer reduce the `/capture/stream`
 * event sequence into this pair so the two paths can't drift on the card/spoken/
 * intent rules. Side-effects (audio playback, reminders, cross-window emits) stay
 * with each caller; this reducer only shapes the turns.
 */
export interface CapturePair {
  user: AgentTurn;
  assistant: AgentTurn;
}

/** Return the pair updated for one stream event. Non-shape events return it unchanged. */
export function reduceCapturePair(pair: CapturePair, event: StreamEvent): CapturePair {
  const { user, assistant } = pair;
  switch (event.type) {
    case "transcript":
      return { user: { ...user, text: event.text, pending: false }, assistant };

    case "token":
      return { user, assistant: { ...assistant, text: assistant.text + event.text } };

    case "tool_call": {
      const steps = [...(assistant.steps ?? []), { name: event.name, summary: "", pending: true }];
      return { user, assistant: { ...assistant, steps } };
    }

    case "tool_result": {
      const steps = [...(assistant.steps ?? [])];
      // Resolve the newest still-pending step with this name (tools can repeat).
      const revIdx = [...steps].reverse().findIndex((s) => s.name === event.name && s.pending);
      if (revIdx !== -1) {
        const realIdx = steps.length - 1 - revIdx;
        steps[realIdx] = { ...steps[realIdx], summary: event.summary, pending: false };
      }
      const nextAssistant: AgentTurn = { ...assistant, steps };
      if (event.name === "surface_cards" && Array.isArray(event.data?.items_data)) {
        nextAssistant.emailCards = event.data.items_data as EmailCard[];
      }
      if (event.name === "surface_calendar" && Array.isArray(event.data?.items_data)) {
        nextAssistant.calendarCards = event.data.items_data as CalendarCard[];
      }
      if (event.name === "surface_tasks" && Array.isArray(event.data?.items_data)) {
        nextAssistant.taskCards = event.data.items_data as TaskCard[];
      }
      return { user, assistant: nextAssistant };
    }

    case "spoken":
      // Final spoken text replaces the streamed token buffer.
      return { user, assistant: { ...assistant, text: event.text } };

    case "metadata":
      return { user: { ...user, intentType: event.intent_type }, assistant };

    case "stored":
      // A stored item with a due time is a reminder; classify_intent tags these as
      // "task", so relabel the badge to match what was actually created.
      if (event.due_at) return { user: { ...user, intentType: "reminder" }, assistant };
      return pair;

    default:
      return pair;
  }
}
