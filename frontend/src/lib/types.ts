export type Role = "user" | "assistant" | "system";

export interface Message {
  role: Role;
  content: string;
}

export interface Token {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export interface UserResponse {
  id: number;
  email: string;
  token: Token;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export interface SessionResponse {
  session_id: string;
  agent_id?: number | null;
  name: string;
  token: Token;
}

export interface DatabaseSummary {
  driver: string;
  host: string;
  port: number;
  database: string;
  username: string;
  sslmode?: string | null;
  password_persisted: boolean;
}

export interface Skill {
  id: number;
  name: string;
  description: string;
  body: string;
  source: string;
}

export interface RegistrySkill {
  slug: string;
  name: string;
  description: string;
}

export interface Agent {
  id: number;
  name: string;
  system_prompt: string;
  web_search: boolean;
  sql: boolean;
  memory: boolean;
  folder?: string | null;
  folder_writable?: boolean;
  database?: DatabaseSummary | null;
  skills: number[];
  config: Record<string, unknown>;
}

export interface ChatResponse {
  messages: Message[];
}

export interface StreamChunk {
  content: string;
  done: boolean;
}

export interface ConnectDbRequest {
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  driver?: string;
  sslmode?: string | null;
}

export interface ConnectDbResponse {
  connected: boolean;
  dialect: string;
  table_count: number;
}

export interface GrantFolderResponse {
  granted: boolean;
  folder: string;
}

export interface SourceStatus {
  db_connected: boolean;
  dialect?: string | null;
  folder?: string | null;
  doc_count?: number;
  page_count?: number;
  indexing?: boolean;
}

// --- Data Agent streaming (observable timeline) ---

/** One task in the agent's plan (from the `write_todos` tool), rendered as a live checklist. */
export interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "completed" | string;
}

export type StreamEvent =
  | { type: "tool_start"; name: string; input?: string }
  | { type: "tool_end"; name: string; output?: string }
  | { type: "token"; content: string }
  | { type: "thinking"; content: string }
  | { type: "todos"; items: TodoItem[] }
  | { type: "hitl_request"; id: number; action_type: string; title?: string; format?: string }
  | { type: "done" }
  | { type: "error"; content?: string };

/** One persisted tool step of an assistant turn (returned with the conversation history). */
export interface HistoryStep {
  name: string;
  input?: string | null;
  output?: string | null;
}

/** A persisted message plus, for assistant turns, its tool-activity steps. */
export interface HistoryMessage {
  role: Role;
  content: string;
  steps: HistoryStep[];
}

/** One entry in a session's episodic audit log (persisted server-side). */
export interface SessionEvent {
  id: number;
  agent_id?: number | null;
  session_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  scope: string;
  created_at: string;
}

/** An outward action awaiting approval, anchored to the assistant turn that requested it. */
export interface TurnApproval {
  id: number;
  title: string;
  format?: string;
  status: "pending" | "approved" | "rejected";
  /** "export_artifact" (default) or "approve_plan" — drives the card's labels and resume behavior. */
  action_type?: string;
}

export interface ToolStep {
  id: number;
  name: string;
  input?: string;
  output?: string;
  done: boolean;
}

export interface UserTurn {
  role: "user";
  content: string;
}

export interface AssistantTurn {
  role: "assistant";
  steps: ToolStep[];
  content: string;
  streaming: boolean;
  error?: string;
  approval?: TurnApproval;
  todos?: TodoItem[];
  /** Live reasoning (Anthropic summarized thinking), streamed before/with the answer. */
  thinking?: string;
}

export type Turn = UserTurn | AssistantTurn;
