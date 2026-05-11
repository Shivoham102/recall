export type AgentTurnRole = "user" | "assistant" | "system";

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
}

export interface TaskCard {
  id: string;
  content: string;
  intent_type: string;
  status: string;
  created_at: string;
  due_hint?: string | null;
}

export interface AgentTurn {
  id: string;
  role: AgentTurnRole;
  text: string;
  intentType?: string;
  steps?: AgentStep[];
  emailCards?: EmailCard[];
  taskCards?: TaskCard[];
  pending?: boolean;
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
}
