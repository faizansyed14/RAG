"use client";

import { ExternalLink, FolderOpen } from "lucide-react";

const RAG_DRIVE_URL = "https://drive.google.com/drive/folders/1O8AoRZD8E4v5FYsBGpwHab52vLOBV9E5";

export function RagFilesView() {
  return (
    <div className="flex h-full min-h-0 items-center justify-center overflow-y-auto bg-background px-4 py-10 sm:px-6">
      <div className="w-full max-w-md text-center">
        <span className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-surface-raised text-muted shadow-soft">
          <FolderOpen className="h-5 w-5" strokeWidth={1.75} />
        </span>
        <h1 className="font-display text-2xl font-medium tracking-[-0.03em] text-foreground">Rag files</h1>
        <p className="mt-3 text-sm leading-6 text-muted">
          This is the raw documents used by RAG.
        </p>
        <a
          href={RAG_DRIVE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-6 inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg transition hover:-translate-y-0.5 hover:shadow-md"
        >
          Open in Google Drive
          <ExternalLink className="h-4 w-4" />
        </a>
      </div>
    </div>
  );
}
