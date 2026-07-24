/** Shared logic for turning a browser folder pick (`<input type="file" webkitdirectory>`) into
 *  an uploadable file list — filtering noise and enforcing size/count limits client-side before
 *  ever hitting the network. The server enforces the same limits authoritatively; this is just
 *  fast feedback. Shared between SourcesPanel (per-session) and AgentsScreen (per-agent). */

export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024;
export const MAX_UPLOAD_FILES = 500;

const NOISE_SEGMENTS = new Set([".git", "node_modules", "__pycache__", ".venv", "venv"]);

function isNoiseSegment(segment: string): boolean {
  return NOISE_SEGMENTS.has(segment) || segment.startsWith(".");
}

/** One file picked from a folder, with its path relative to the folder root. */
export interface PickedFile {
  file: File;
  relativePath: string;
}

export interface FilteredFolder {
  files: PickedFile[];
  totalBytes: number;
  skipped: number;
}

/** Filters out VCS/dependency/cache/hidden entries from a browser folder pick. */
export function filterFolderFiles(fileList: FileList): FilteredFolder {
  const files: PickedFile[] = [];
  let totalBytes = 0;
  let skipped = 0;
  for (const file of Array.from(fileList)) {
    const full = file.webkitRelativePath || file.name;
    const segments = full.split("/");
    if (segments.some(isNoiseSegment)) {
      skipped++;
      continue;
    }
    // Drop the top-level folder name the user picked — the upload root should mirror the
    // folder's contents, not nest everything one level deeper under its own name.
    const relativePath = segments.slice(1).join("/") || segments[0];
    files.push({ file, relativePath });
    totalBytes += file.size;
  }
  return { files, totalBytes, skipped };
}

/** A user-facing error if the filtered folder exceeds upload limits, else null. */
export function validateFolderSize(filtered: FilteredFolder): string | null {
  if (filtered.files.length === 0) return "Nenhum arquivo relevante encontrado nessa pasta.";
  if (filtered.files.length > MAX_UPLOAD_FILES) {
    return `Muitos arquivos (${filtered.files.length}) — máximo ${MAX_UPLOAD_FILES}.`;
  }
  if (filtered.totalBytes > MAX_UPLOAD_BYTES) {
    const mb = (filtered.totalBytes / (1024 * 1024)).toFixed(0);
    return `Pasta grande demais (${mb}MB) — máximo ${MAX_UPLOAD_BYTES / (1024 * 1024)}MB.`;
  }
  return null;
}
