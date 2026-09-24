"use client";

import { useCallback, useRef, useState } from "react";
import clsx from "clsx";
import { CheckCircle2, FileUp, Loader2, TriangleAlert } from "lucide-react";
import { streamProgress, uploadDocument } from "@/lib/api";
import type { ProgressEvent } from "@/lib/types";

interface UploadItem {
  id: string;
  name: string;
  documentId?: string;
  phase: ProgressEvent["phase"] | "uploading";
  page?: number;
  total?: number;
  caption?: string | null;
  error?: string;
}

interface Props {
  onIndexed: () => void;
  folderId?: string | null;
}

const PHASE_LABEL: Record<string, string> = {
  uploading: "Uploading",
  extracting: "Extracting content",
  ocr: "Reading visual pages",
  embedding: "Embedding diagrams",
  indexing: "Building search index",
  indexed: "Ready to search",
  failed: "Upload failed",
};

function progressFor(item: UploadItem): number {
  if (item.phase === "uploading") return 12;
  if (item.phase === "extracting") return 28;
  if (item.phase === "ocr") {
    if (item.page && item.total) return 32 + Math.round((item.page / item.total) * 34);
    return 48;
  }
  if (item.phase === "embedding") return 74;
  if (item.phase === "indexing") return 88;
  if (item.phase === "indexed") return 100;
  return 0;
}

export function FileDropzone({ onIndexed, folderId }: Props) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const updateItem = (id: string, update: Partial<UploadItem>) => {
    setItems((previous) => previous.map((item) => (item.id === id ? { ...item, ...update } : item)));
  };

  const handleFiles = useCallback(
    async (files: FileList) => {
      for (const file of Array.from(files)) {
        const id = crypto.randomUUID();
        setItems((previous) => [...previous, { id, name: file.name, phase: "uploading" }]);
        try {
          const { document_id } = await uploadDocument(file, folderId);
          updateItem(id, { documentId: document_id, phase: "extracting" });

          await streamProgress(document_id, (event) => {
            updateItem(id, {
              phase: event.phase,
              page: event.page,
              total: event.total,
              caption: event.caption,
              error: event.error,
            });
            if (event.phase === "indexed") onIndexed();
          });
        } catch {
          updateItem(id, { phase: "failed", error: "The file could not be uploaded. Please try again." });
        }
      }
    },
    [onIndexed, folderId],
  );

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (event.dataTransfer.files.length) void handleFiles(event.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
        }}
        className={clsx(
          "group cursor-pointer rounded-xl border border-dashed px-5 py-10 text-center transition sm:px-10 sm:py-12",
          dragging
            ? "border-foreground bg-surface ring-4 ring-border"
            : "border-border-strong bg-surface hover:border-foreground",
        )}
      >
        <div className={clsx("mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl transition", dragging ? "scale-105 bg-foreground text-accent-fg" : "bg-background text-foreground shadow-soft ring-1 ring-border")}>
          <FileUp className="h-6 w-6" strokeWidth={1.7} />
        </div>
        <p className="text-sm font-semibold text-foreground">{dragging ? "Drop files to begin" : "Drop documents here"}</p>
        <p className="mt-1.5 text-xs leading-5 text-muted">or click to choose files from your computer</p>
        <div className="mx-auto mt-4 flex max-w-md flex-wrap justify-center gap-1.5">
          {["PDF", "DOCX", "CSV", "XLSX", "EML", "TXT", "JSON", "XER"].map((type) => (
            <span key={type} className="rounded-md border border-border bg-surface-raised px-1.5 py-0.5 text-[9px] font-semibold text-muted">{type}</span>
          ))}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.csv,.xlsx,.eml,.txt,.json,.xer"
          multiple
          hidden
          onChange={(event) => {
            const files = event.target.files;
            if (files) void handleFiles(files);
            event.currentTarget.value = "";
          }}
        />
      </div>

      {items.length > 0 && (
        <div className="mt-5">
          <div className="mb-2 flex items-center justify-between px-1">
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">Upload queue</span>
            <span className="text-[10px] text-muted">{items.length} file{items.length === 1 ? "" : "s"}</span>
          </div>
          <ul className="space-y-2">
            {items.map((item) => {
              const progress = progressFor(item);
              return (
                <li key={item.id} className="rounded-xl border border-border bg-surface-raised p-3.5 shadow-soft">
                  <div className="flex items-start gap-3">
                    <div
                      className={clsx(
                        "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
                        item.phase === "indexed" && "bg-success-soft text-success",
                        item.phase === "failed" && "bg-danger-soft text-danger",
                        item.phase !== "indexed" && item.phase !== "failed" && "bg-surface text-foreground",
                      )}
                    >
                      {item.phase === "indexed" ? (
                        <CheckCircle2 className="h-4 w-4" />
                      ) : item.phase === "failed" ? (
                        <TriangleAlert className="h-4 w-4" />
                      ) : (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate text-xs font-semibold text-foreground/80">{item.name}</span>
                        <span
                          className={clsx(
                            "shrink-0 text-[10px] font-medium",
                            item.phase === "failed" && "text-danger",
                            item.phase === "indexed" && "text-success",
                            item.phase !== "failed" && item.phase !== "indexed" && "text-foreground",
                          )}
                        >
                          {PHASE_LABEL[item.phase] ?? item.phase}
                        </span>
                      </div>
                      {item.phase !== "failed" && (
                        <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-surface">
                          <div
                            className={clsx("h-full rounded-full transition-all duration-500", item.phase === "indexed" ? "bg-success" : "bg-accent")}
                            style={{ width: `${progress}%` }}
                          />
                        </div>
                      )}
                      {item.phase === "ocr" && item.total ? (
                        <div className="mt-2 truncate text-[10px] text-muted">
                          Page {item.page ?? 0} of {item.total}{item.caption ? ` · ${item.caption}` : ""}
                        </div>
                      ) : null}
                      {item.error && <div className="mt-2 text-[10px] text-danger">{item.error}</div>}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
