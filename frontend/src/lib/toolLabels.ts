/** Friendly, present-continuous labels for the agent's tools — the "behind the scenes" activity
 *  the user sees in the chat and the timeline ("buscando", "gerando", "escrevendo", …). */
export interface ToolLabel {
  icon: string;
  label: string;
}

const TOOL_LABELS: Record<string, ToolLabel> = {
  list_tables: { icon: "🗂️", label: "Listando tabelas" },
  describe_tables: { icon: "🔎", label: "Lendo o schema" },
  run_sql: { icon: "🛢️", label: "Consultando o banco" },
  ls: { icon: "📁", label: "Listando arquivos" },
  read_file: { icon: "📄", label: "Lendo arquivo" },
  write_file: { icon: "✍️", label: "Escrevendo arquivo" },
  glob: { icon: "🔦", label: "Procurando arquivos" },
  grep: { icon: "🔍", label: "Buscando no conteúdo" },
  write_todos: { icon: "🧠", label: "Planejando" },
  buscar_memoria: { icon: "🧭", label: "Buscando na memória" },
  gerar_artefato: { icon: "📝", label: "Gerando documento" },
  gerar_planilha: { icon: "📊", label: "Gerando planilha" },
};

export function labelFor(name: string): ToolLabel {
  return TOOL_LABELS[name] ?? { icon: "🔧", label: name };
}
