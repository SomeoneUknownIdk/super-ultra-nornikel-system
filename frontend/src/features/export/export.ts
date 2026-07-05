import type { ExportFormat, SearchResponse } from "../../domain/types";
import { api } from "../../api/api-provider";

const slug = (value: string) => value.toLowerCase().replace(/[^a-zа-яё0-9]+/gi, "-").replace(/^-|-$/g, "").slice(0, 54) || "research";
const fileName = (query: string, ext: string) => `${new Date().toISOString().slice(0, 10)}-${slug(query)}.${ext}`;

/**
 * Экспорт результата поиска через `POST /api/export/{format}` (API.md §6).
 * Тело — объект ответа `/api/search` целиком; ответ — файл (`Content-Disposition: attachment`).
 */
export async function downloadExport(format: ExportFormat, payload: SearchResponse): Promise<void> {
  const blob = await api.exportResult(format, payload);
  const ext = format === "jsonld" ? "jsonld" : format;
  triggerDownload(blob, fileName(payload.answer_md.split("\n")[0] || payload.intent, ext));
}

function triggerDownload(content: Blob, name: string) {
  const url = URL.createObjectURL(content);
  const link = document.createElement("a");
  link.href = url; link.download = name; link.click();
  URL.revokeObjectURL(url);
}