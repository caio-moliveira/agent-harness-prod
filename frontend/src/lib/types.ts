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
  name: string;
  token: Token;
}

export interface ChatResponse {
  messages: Message[];
}

export interface StreamChunk {
  content: string;
  done: boolean;
}
