import { useCallback, useId, useRef, useState } from "react";
import { fileExtension, formatBytes } from "../lib/format";

interface Props {
  label: string;
  hint: string;
  accept: string[];
  file: File | null;
  onSelect: (file: File | null) => void;
  maxBytes?: number;
  optional?: boolean;
  disabled?: boolean;
}

/**
 * Drag-and-drop / click file picker.
 *
 * Validation happens here so the user hears about an unusable file before any
 * upload is attempted — rejecting a 400 MB GeoTIFF after sending it is a
 * minute of the user's time spent to learn something knowable immediately.
 * The server validates again regardless; this check is a courtesy, not a
 * security boundary.
 */
export function Dropzone({
  label,
  hint,
  accept,
  file,
  onSelect,
  maxBytes,
  optional,
  disabled,
}: Props) {
  const inputId = useId();
  const errorId = `${inputId}-error`;
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const validate = useCallback(
    (candidate: File): string | null => {
      const extension = fileExtension(candidate.name);
      if (accept.length && !accept.includes(extension)) {
        return `${extension || "That file"} is not supported here. Expected: ${accept.join(", ")}`;
      }
      if (maxBytes && candidate.size > maxBytes) {
        return `File is ${formatBytes(candidate.size)}; the server limit is ${formatBytes(maxBytes)}.`;
      }
      if (candidate.size === 0) {
        return "That file is empty.";
      }
      return null;
    },
    [accept, maxBytes],
  );

  const handleFiles = useCallback(
    (files: FileList | null) => {
      const candidate = files?.[0];
      if (!candidate) return;
      const problem = validate(candidate);
      setError(problem);
      onSelect(problem ? null : candidate);
    },
    [onSelect, validate],
  );

  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="label">{label}</h2>
        {optional && <span className="text-[11px] text-ink-faint">Optional</span>}
      </div>

      {file ? (
        <div className="panel flex items-center justify-between gap-3 rounded-md px-3 py-2.5">
          <div className="min-w-0">
            <p className="truncate text-[13px] text-ink" title={file.name}>
              {file.name}
            </p>
            <p className="tabular mt-0.5 font-mono text-[11px] text-ink-faint">
              {formatBytes(file.size)} · {fileExtension(file.name).replace(".", "").toUpperCase()}
              {file.type ? ` · ${file.type}` : ""}
            </p>
          </div>
          <button
            type="button"
            disabled={disabled}
            aria-label={`Remove ${file.name}`}
            className="shrink-0 rounded px-2 py-1 text-[12px] text-ink-muted hover:bg-surface-2 hover:text-ink disabled:cursor-not-allowed disabled:text-ink-faint disabled:hover:bg-transparent"
            onClick={() => {
              setError(null);
              onSelect(null);
              if (inputRef.current) inputRef.current.value = "";
            }}
          >
            Remove
          </button>
        </div>
      ) : (
        <label
          htmlFor={inputId}
          onDragOver={(event) => {
            if (disabled) return;
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            if (disabled) return;
            event.preventDefault();
            setDragging(false);
            handleFiles(event.dataTransfer.files);
          }}
          className={`flex flex-col items-center justify-center rounded-md border border-dashed px-3 py-6 text-center transition-colors ${
            disabled
              ? "cursor-not-allowed border-line text-ink-faint"
              : dragging
                ? "cursor-pointer border-accent bg-accent/5"
                : "cursor-pointer border-line-strong bg-surface-1 hover:border-ink-faint"
          }`}
        >
          <span className="text-[13px] text-ink-muted">{hint}</span>
          <span className="mt-1 text-[11px] text-ink-faint">{accept.join(" · ")}</span>
          <input
            id={inputId}
            ref={inputRef}
            type="file"
            className="sr-only"
            accept={accept.join(",")}
            disabled={disabled}
            aria-describedby={error ? errorId : undefined}
            aria-invalid={error ? true : undefined}
            onChange={(event) => handleFiles(event.target.files)}
          />
        </label>
      )}

      {error && (
        <p id={errorId} role="alert" className="mt-2 text-[12px] leading-relaxed text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
