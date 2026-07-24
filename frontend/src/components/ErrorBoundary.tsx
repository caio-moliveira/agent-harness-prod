import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Last-resort guard: without this, any render-time exception unmounts the whole React tree to a
 * blank white page with no recovery. React error boundaries must be class components — there is
 * no hook equivalent (getDerivedStateFromError / componentDidCatch have no hook form).
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("ui_render_crashed", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-full items-center justify-center p-4">
          <div className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900/70 p-8 text-center shadow-2xl shadow-slate-950/60 backdrop-blur">
            <p className="text-lg font-semibold text-slate-100">Algo deu errado</p>
            <p className="mt-2 text-sm text-slate-400">
              A interface encontrou um erro inesperado. Recarregar a página deve resolver — sua
              conversa fica salva no servidor.
            </p>
            <button
              onClick={() => window.location.reload()}
              className="mt-5 w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-[#000814] transition hover:bg-indigo-500 hover:shadow-[0_0_18px_rgba(0,194,224,0.55)]"
            >
              Recarregar
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
