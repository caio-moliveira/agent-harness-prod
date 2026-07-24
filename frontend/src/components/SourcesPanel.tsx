import { useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import { filterFolderFiles, validateFolderSize } from "../lib/folderUpload";
import type { SourceStatus } from "../lib/types";

export default function SourcesPanel({ onClose }: { onClose: () => void }) {
  const { sessionToken } = useAuth();
  const [status, setStatus] = useState<SourceStatus>({ db_connected: false });
  const [error, setError] = useState<string | null>(null);

  // DB form
  const [host, setHost] = useState("127.0.0.1");
  const [port, setPort] = useState(5432);
  const [database, setDatabase] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [driver, setDriver] = useState("postgresql");
  const [connecting, setConnecting] = useState(false);

  // Folder form
  const [folderPath, setFolderPath] = useState("");
  const [granting, setGranting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const folderInputRef = useRef<HTMLInputElement>(null);

  async function refreshStatus() {
    if (!sessionToken) return;
    try {
      setStatus(await api.dataStatus(sessionToken));
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    void refreshStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionToken]);

  // While the granted folder is being ingested in the background, poll so the chip flips from
  // "indexando…" to the final document/page counts without the user reopening the panel.
  useEffect(() => {
    if (!status.indexing) return;
    const id = setInterval(() => void refreshStatus(), 3000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status.indexing]);

  async function handleConnect() {
    if (!sessionToken) return;
    setError(null);
    setConnecting(true);
    try {
      const res = await api.connectDb(sessionToken, { host, port, database, username, password, driver });
      setPassword("");
      setError(`Conectado (${res.dialect}, ${res.table_count} tabelas).`);
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao conectar");
    } finally {
      setConnecting(false);
    }
  }

  async function handleGrant() {
    if (!sessionToken || !folderPath.trim()) return;
    setError(null);
    setGranting(true);
    try {
      await api.grantFolder(sessionToken, folderPath.trim());
      setError("Pasta autorizada.");
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao autorizar a pasta");
    } finally {
      setGranting(false);
    }
  }

  async function handleFolderPicked(e: React.ChangeEvent<HTMLInputElement>) {
    const fileList = e.target.files;
    e.target.value = ""; // allow re-picking the same folder later
    if (!sessionToken || !fileList || fileList.length === 0) return;

    const filtered = filterFolderFiles(fileList);
    const sizeError = validateFolderSize(filtered);
    if (sizeError) {
      setError(sizeError);
      return;
    }

    setError(null);
    setUploading(true);
    try {
      await api.uploadFolder(sessionToken, filtered.files);
      setError(`Pasta enviada (${filtered.files.length} arquivo(s)).`);
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao enviar a pasta");
    } finally {
      setUploading(false);
    }
  }

  async function handleDisconnect() {
    if (!sessionToken) return;
    await api.disconnectSources(sessionToken).catch(() => undefined);
    await refreshStatus();
  }

  const hasSource = status.db_connected || Boolean(status.folder);

  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        className="flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-slate-800 bg-slate-900 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">Fontes de dados</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
            ✕
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Configure o que o agente pode acessar. Credenciais ficam apenas na memória do servidor
          (nunca gravadas em disco ou logs).
        </p>

        {/* Status */}
        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <span className={`rounded-full px-2 py-1 ${status.db_connected ? "bg-emerald-900 text-emerald-200" : "bg-slate-800 text-slate-400"}`}>
            Banco: {status.db_connected ? status.dialect : "não conectado"}
          </span>
          <span className={`rounded-full px-2 py-1 ${status.folder ? "bg-emerald-900 text-emerald-200" : "bg-slate-800 text-slate-400"}`}>
            Pasta: {status.folder ? "autorizada (somente leitura)" : "nenhuma"}
          </span>
          {status.folder &&
            (status.indexing ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-indigo-950/60 px-2 py-1 text-indigo-200 ring-1 ring-inset ring-indigo-800/50">
                <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-indigo-700 border-t-indigo-300" />
                Indexando documentos…
              </span>
            ) : (
              (status.doc_count ?? 0) > 0 && (
                <span className="rounded-full bg-slate-800 px-2 py-1 text-slate-300">
                  📚 {status.doc_count} doc(s) · {status.page_count ?? 0} págs · indexado
                </span>
              )
            ))}
          {hasSource && (
            <button onClick={handleDisconnect} className="rounded-full bg-red-950 px-2 py-1 text-red-300 hover:bg-red-900">
              Desconectar tudo
            </button>
          )}
        </div>

        {status.folder && (
          <p className="mt-2 break-all rounded-lg bg-slate-950 px-3 py-2 text-[11px] text-slate-400">
            📁 {status.folder}
            <span className="ml-1 text-slate-500">— exposta em <code>/workspace</code>, somente leitura.</span>
          </p>
        )}

        {/* DB form */}
        <section className="mt-5 space-y-2 rounded-xl border border-slate-800 p-4">
          <h3 className="text-sm font-medium">Conectar banco (read-only)</h3>
          <div className="grid grid-cols-3 gap-2">
            <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="host" className="col-span-2 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm outline-none focus:border-indigo-500" />
            <input value={port} onChange={(e) => setPort(Number(e.target.value) || 5432)} type="number" placeholder="porta" className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm outline-none focus:border-indigo-500" />
          </div>
          <input value={database} onChange={(e) => setDatabase(e.target.value)} placeholder="banco" className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm outline-none focus:border-indigo-500" />
          <div className="grid grid-cols-2 gap-2">
            <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="usuário" autoComplete="off" className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm outline-none focus:border-indigo-500" />
            <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" placeholder="senha" autoComplete="new-password" className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm outline-none focus:border-indigo-500" />
          </div>
          <select value={driver} onChange={(e) => setDriver(e.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm outline-none focus:border-indigo-500">
            <option value="postgresql">PostgreSQL</option>
            <option value="mysql+pymysql">MySQL</option>
          </select>
          <button onClick={handleConnect} disabled={connecting || !database || !username} className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-[#000814] transition hover:bg-indigo-500 hover:shadow-[0_0_18px_rgba(0,194,224,0.55)] disabled:opacity-50 disabled:shadow-none">
            {connecting ? "Conectando…" : "Conectar"}
          </button>
        </section>

        {/* Folder form */}
        <section className="mt-4 space-y-2 rounded-xl border border-slate-800 p-4">
          <h3 className="text-sm font-medium">Selecionar pasta (somente leitura)</h3>
          <input
            ref={folderInputRef}
            type="file"
            /* @ts-expect-error webkitdirectory has no TS typing but is supported by every major browser */
            webkitdirectory=""
            multiple
            onChange={(e) => void handleFolderPicked(e)}
            className="hidden"
          />
          <button
            onClick={() => folderInputRef.current?.click()}
            disabled={uploading}
            className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-[#000814] transition hover:bg-indigo-500 hover:shadow-[0_0_18px_rgba(0,194,224,0.55)] disabled:opacity-50 disabled:shadow-none"
          >
            {uploading ? "Enviando…" : "Selecionar pasta…"}
          </button>
          <p className="text-[11px] text-slate-500">
            Abre o seletor de pasta do seu computador — os arquivos são enviados e o agente passa a
            lê-los em <code>/workspace</code>, apenas para leitura (nunca grava nem executa comandos).
          </p>

          <details className="pt-1">
            <summary className="cursor-pointer text-[11px] text-slate-500 hover:text-slate-300">
              Avançado: apontar para um caminho no servidor
            </summary>
            <div className="mt-2 space-y-2">
              <input value={folderPath} onChange={(e) => setFolderPath(e.target.value)} placeholder="D:/caminho/para/a/pasta" className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm outline-none focus:border-indigo-500" />
              <button onClick={handleGrant} disabled={granting || !folderPath.trim()} className="w-full rounded-lg border border-slate-600 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50">
                {granting ? "Autorizando…" : "Autorizar"}
              </button>
              <p className="text-[11px] text-slate-500">
                Só pastas sob as raízes configuradas no servidor (SANDBOX_ALLOWED_ROOTS) são permitidas.
              </p>
            </div>
          </details>
        </section>

        {error && (
          <div className="mt-3 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-300">{error}</div>
        )}
      </aside>
    </div>
  );
}
