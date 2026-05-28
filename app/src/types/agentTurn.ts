export type AgentTurnRole = "user" | "assistant" | "system" | "proactive";

export interface AgentStep {
  name: string;
  summary: string;
  pending: boolean;
}

export interface EmailCard {
  sender: string;
  subject: string;
  snippet: string;
  received: string;
  unread: boolean;
  important: boolean;
  link: string;
}

export interface CalendarCard {
  title: string;
  start: string;
  end: string;
  link: string;
  location: string;
  is_all_day: boolean;
}

export interface TaskCard {
  id: string;
  content: string;
  intent_type: string;
  status: string;
  created_at: string;
  due_hint?: string | null;
  due_at?: string | null;
  display_text?: string | null;
  link?: string | null;
}

export interface AgentTurn {
  id: string;
  role: AgentTurnRole;
  text: string;
  intentType?: string;
  steps?: AgentStep[];
  emailCards?: EmailCard[];
  calendarCards?: CalendarCard[];
  taskCards?: TaskCard[];
  /** Draft follow-up cards grouped under the same-day morning brief. */
  morningBriefDraftCards?: TaskCard[];
  pending?: boolean;
  /** ISO timestamp for proactive turns */
  timestamp?: string;
  /** Base64 MP3 for morning_brief TTS announcement; present only when TTS succeeded */
  audioB64?: string;
}

export interface LastCaptureMeta {
  intent_type?: string | null;
  item_id?: string | null;
  due_at?: string | null;
  captured_at?: string;
}

export interface AgentChat {
  id: string;
  user_id: string;
  agent_session_id: string;
  title: string | null;
  turns: AgentTurn[];
  last_capture: LastCaptureMeta | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  is_proactive_inbox?: boolean;
}
