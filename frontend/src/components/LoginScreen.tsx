import { useState } from "react";
import type { FormEvent } from "react";
import { useAuth } from "../context/AuthContext";
import { IconSparkles } from "./icons";

type Mode = "login" | "register";

export default function LoginScreen() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha na autenticação");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-full items-center justify-center p-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900/70 p-8 shadow-2xl shadow-slate-950/60 backdrop-blur">
        <div className="mb-5 flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow-lg shadow-indigo-950/50">
            <IconSparkles className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Data Agent</h1>
            <p className="text-xs text-slate-500">Seu assistente de dados</p>
          </div>
        </div>
        <p className="text-sm text-slate-400">
          {mode === "login" ? "Entre para conversar com o agente." : "Crie uma conta para começar."}
        </p>

        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <div>
            <label className="mb-1 block text-sm text-slate-300">E-mail</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-indigo-500"
              placeholder="voce@exemplo.com"
              autoComplete="email"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm text-slate-300">Senha</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-indigo-500"
              placeholder="••••••••"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
            {mode === "register" && (
              <p className="mt-1 text-xs text-slate-500">
                Mínimo 8 caracteres, com maiúscula, minúscula, número e símbolo.
              </p>
            )}
          </div>

          {error && (
            <div className="rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-300">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-[#000814] transition hover:bg-indigo-500 hover:shadow-[0_0_18px_rgba(0,194,224,0.55)] disabled:opacity-50 disabled:shadow-none"
          >
            {loading ? "Aguarde…" : mode === "login" ? "Entrar" : "Criar conta"}
          </button>
        </form>

        <button
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
          className="mt-4 w-full text-center text-sm text-slate-400 hover:text-slate-200"
        >
          {mode === "login" ? "Não tem conta? Cadastre-se" : "Já tem conta? Entrar"}
        </button>
      </div>
    </div>
  );
}
