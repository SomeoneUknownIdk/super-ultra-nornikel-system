import { useEffect, useRef, useState } from "react";
import { FileUp, UploadCloud, X } from "lucide-react";
import { api } from "../../api/api-provider";
import type { UploadProgress } from "../../domain/types";
import s from "../../styles/ui.module.css";

const accepted = ["pdf","doc","docx","docm","pptx","xls","xlsx","txt"];
export function ImportDialog({ onClose, onComplete }: { onClose: () => void; onComplete: () => void }) {
  const [files, setFiles] = useState<File[]>([]); const [progress, setProgress] = useState<Record<string, UploadProgress>>({}); const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const inputRef = useRef<HTMLInputElement>(null); const dialogRef = useRef<HTMLElement>(null); const controllerRef = useRef<AbortController | null>(null);
  useEffect(() => {
    const dialog = dialogRef.current; const focusable = () => [...(dialog?.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled])") ?? [])];
    focusable()[0]?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { controllerRef.current?.abort(); onClose(); }
      if (event.key === "Tab") { const items = focusable(); if (!items.length) return; const first = items[0]; const last = items[items.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }
    };
    document.addEventListener("keydown", keydown); return () => document.removeEventListener("keydown", keydown);
  }, [onClose]);
  function add(list: FileList | null) { const next = [...(list ? Array.from(list) : [])]; const invalid = next.find((file) => !accepted.includes(file.name.split(".").pop()?.toLowerCase() ?? "") || file.size > 50 * 1024 * 1024); if (invalid) { setError(`Файл ${invalid.name} имеет неподдерживаемый формат или превышает 50 MB`); return; } if (files.length + next.length > 10) { setError("За одну операцию можно загрузить не более 10 файлов"); return; } setError(""); setFiles((current) => [...current, ...next]); }
  async function upload() { const controller = new AbortController(); controllerRef.current = controller; setBusy(true); setError(""); try { await api.uploadDocuments(files, (event) => setProgress((current) => ({ ...current, [event.fileName]: event })), controller.signal); onComplete(); } catch (reason) { if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(reason instanceof Error ? reason.message : "Ошибка импорта"); } finally { setBusy(false); controllerRef.current = null; } }
  function cancel() { controllerRef.current?.abort(); onClose(); }
  return <div className={s.modalBackdrop} role="presentation"><section ref={dialogRef} className={s.modal} role="dialog" aria-modal="true" aria-labelledby="import-title"><header><div><span className={s.modalIcon}><FileUp/></span><div><h2 id="import-title">Добавить источники</h2><p>Система распознает структуру и добавит факты в граф</p></div></div><button onClick={cancel} aria-label="Закрыть"><X/></button></header><div className={s.modalBody}><button className={s.dropzone} onClick={() => inputRef.current?.click()} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); add(e.dataTransfer.files); }}><UploadCloud/><b>Перетащите документы сюда</b><span>или выберите файлы · PDF, DOCX, PPTX, XLSX · до 50 MB</span></button><input ref={inputRef} hidden multiple type="file" accept={accepted.map((e) => `.${e}`).join(",")} onChange={(e) => add(e.target.files)}/>{files.length > 0 && <div className={s.uploadFiles}>{files.map((file) => { const item = progress[file.name]; return <div key={file.name}><FileUp/><span><b>{file.name}</b><small>{item ? stageLabel(item.stage) : `${(file.size / 1024 / 1024).toFixed(1)} MB`}</small>{item && <i><em style={{ width: `${item.percent}%` }}/></i>}</span>{!busy && <button onClick={() => setFiles((current) => current.filter((f) => f !== file))}><X/></button>}</div>; })}</div>}{error && <p className={s.formError}>{error}</p>}</div><footer><button className={s.ghostButton} onClick={cancel}>{busy ? "Остановить" : "Отмена"}</button><button className={s.primaryButton} disabled={!files.length || busy} onClick={upload}>{busy ? "Обрабатываем…" : `Добавить ${files.length || ""}`}</button></footer></section></div>;
}
function stageLabel(stage: UploadProgress["stage"]) { return ({ upload: "Загрузка", extraction: "Извлечение фактов", complete: "Готово" })[stage]; }
