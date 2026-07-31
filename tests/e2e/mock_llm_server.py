"""Scripted OpenAI-compatible mock LLM that drives the deepagents loop with real tool calls.

Speaks ``/v1/chat/completions`` (streaming SSE and plain JSON), so the API under test reaches it
via ``MODEL=openai:<anything>`` + ``OPENAI_BASE_URL`` — the exact provider plumbing production
uses, with zero tokens spent. Scenario keywords embedded in the user message select the script:

- (default)            first round calls ``ls`` on /workspace, then answers with the tool output
- ``SEMPREFERRAMENTA`` always returns a tool call → runaway loop (must end at the call cap)
- ``TRAVE60``          hangs 60s before answering → must trip the turn wall-clock timeout
- ``GEREARTEFATO``     calls ``gerar_artefato`` (docx) → exercises the HITL/artifact flow
- safety-evaluator prompts always get "SAFE"
"""

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def pick_response(body: dict) -> dict:
    """Decide the assistant message for a request: ``{"content": ...}`` or ``{"tool_calls": ...}``."""
    messages = body.get("messages", [])
    tools = body.get("tools") or []
    tool_names = [t.get("function", {}).get("name", "") for t in tools]
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), {})
    last_content = last_user.get("content") or ""
    if isinstance(last_content, list):  # content blocks
        last_content = " ".join(b.get("text", "") for b in last_content if isinstance(b, dict))

    if "safety evaluator" in last_content.lower():
        return {"content": "SAFE"}

    all_user_text = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
    had_tool_result = any(m.get("role") == "tool" for m in messages)

    if "TRAVE60" in all_user_text:
        time.sleep(60)
        return {"content": "acordei"}
    if "SEMPREFERRAMENTA" in all_user_text and "ls" in tool_names:
        return {"tool_calls": [{"name": "ls", "arguments": {"path": "/workspace"}}]}
    if "GEREARTEFATO" in all_user_text:
        if not had_tool_result and "gerar_artefato" in tool_names:
            return {"tool_calls": [{"name": "gerar_artefato", "arguments": {
                "titulo": "Relatório E2E",
                "formato": "docx",
                "secoes": [{"titulo": "Resumo", "itens": [
                    {"texto": "Widget A vendeu 18 unidades.", "fonte": "vendas.csv"},
                ]}],
            }}]}
        return {"content": "Preparei o relatório; aguarde a aprovação para baixar."}

    if tool_names and not had_tool_result and "ls" in tool_names:
        return {"tool_calls": [{"name": "ls", "arguments": {"path": "/workspace"}}]}
    if had_tool_result:
        tool_outputs = " | ".join(str(m.get("content", ""))[:300] for m in messages if m.get("role") == "tool")
        return {"content": f"Conteúdo visto pelas ferramentas: {tool_outputs[:600]}"}
    return {"content": "Resposta simples do mock (sem ferramentas)."}


def _to_openai_message(resp: dict) -> dict:
    msg = {"role": "assistant", "content": resp.get("content")}
    if "tool_calls" in resp:
        msg["content"] = None
        msg["tool_calls"] = [
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
            }
            for tc in resp["tool_calls"]
        ]
    return msg


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server API
        """Serve one /v1/chat/completions request (streaming or plain)."""
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.loads(raw or b"{}")
        resp = pick_response(body)
        message = _to_openai_message(resp)
        finish = "tool_calls" if "tool_calls" in resp else "stop"
        model = body.get("model", "mock")

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            base = {"id": "chatcmpl-mock", "object": "chat.completion.chunk", "created": int(time.time()), "model": model}

            def chunk(delta, finish_reason=None):
                c = dict(base, choices=[{"index": 0, "delta": delta, "finish_reason": finish_reason}])
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())

            chunk({"role": "assistant"})
            if message.get("tool_calls"):
                for i, tc in enumerate(message["tool_calls"]):
                    chunk({"tool_calls": [{"index": i, "id": tc["id"], "type": "function",
                                           "function": {"name": tc["function"]["name"], "arguments": ""}}]})
                    chunk({"tool_calls": [{"index": i, "function": {"arguments": tc["function"]["arguments"]}}]})
            else:
                text = message["content"] or ""
                for i in range(0, len(text), 24):
                    chunk({"content": text[i:i + 24]})
            chunk({}, finish_reason=finish)
            usage = dict(base, choices=[], usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70})
            self.wfile.write(f"data: {json.dumps(usage)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            payload = json.dumps({
                "id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def log_message(self, *args):  # noqa: D102 - silence default request logging
        pass


def start(port: int = 0) -> ThreadingHTTPServer:
    """Start the mock server on ``port`` (0 = ephemeral) in a daemon thread; returns the server."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
