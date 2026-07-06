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

export interface Agent {
  id: number;
  name: string;
  system_prompt: string;
  folder?: string | null;
  database?: DatabaseSummary | null;
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
}

// --- Data Agent streaming (observable timeline) ---

export type StreamEvent =
  | { type: "tool_start"; name: string; input?: string }
  | { type: "tool_end"; name: string; output?: string }
  | { type: "token"; content: string }
  | { type: "done" }
  | { type: "error"; content?: string };

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
}

export type Turn = UserTurn | AssistantTurn;
