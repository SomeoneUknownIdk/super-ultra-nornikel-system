import type {
  ConfidenceLevel,
  DemoRole,
  EntityType,
  RelationType,
  SearchIntent,
  Unit,
} from "./types";

export const roleLabels: Record<DemoRole, string> = {
  researcher: "Исследователь",
  analyst: "Аналитик",
  project_lead: "Руководитель проекта",
  admin: "Администратор",
  external_partner: "Внешний партнёр",
};

export const intentLabels: Record<SearchIntent, string> = {
  numeric: "Числовой разбор",
  search: "Поиск по корпусу",
  expert: "Экспертный поиск",
  listing: "Перечень фактов",
};

export const confidenceLabels: Record<ConfidenceLevel, string> = {
  высокая: "Высокая",
  средняя: "Средняя",
  низкая: "Низкая",
};

/** Канон unit → отображение (см. API.md §1). */
export const unitLabels: Record<Unit, string> = {
  pct: "%",
  mg_L: "мг/л",
  g_t: "г/т",
  degC: "°C",
  pH: "pH",
  A_m2: "А/м²",
  t_day: "т/сут",
  m3_h: "м³/ч",
};

export const entityLabels: Record<EntityType, string> = {
  Material: "Материал",
  Process: "Процесс",
  Equipment: "Оборудование",
  Property: "Свойство",
  Experiment: "Эксперимент",
  Publication: "Публикация",
  Expert: "Эксперт",
  Facility: "Площадка",
  Document: "Документ",
  Parameter: "Параметр",
  Phase: "Фаза",
  Condition: "Условие",
  Domain: "Дисциплина",
  Claim: "Тезис",
  Author: "Автор",
  Topic: "Тема",
};

export const geographyLabels: Record<string, string> = {
  RU: "Россия",
  WORLD: "Мир",
};

export const sourceTypeLabels: Record<string, string> = {
  article: "Статья",
  scientific_article: "Научная статья",
  patent: "Патент",
  standard: "Стандарт",
  report: "Отчёт",
  review: "Обзор",
  presentation: "Презентация",
  reference: "Справочник",
  other: "Прочее",
};

/** Уровень доступа/чувствительности — человекочитаемо (вместо сырых internal/public). */
export const sensitivityLabels: Record<string, string> = {
  public: "Публичный",
  internal: "Внутренний",
  restricted: "Ограниченный",
  secret: "Секретный",
};

/** Человекочитаемые виды расхождений (вместо сырых ru_vs_world / method_vs_method). */
export const contradictionKindLabels: Record<string, string> = {
  ru_vs_world: "Россия vs мир",
  method_vs_method: "Метод vs метод",
};

export const relationLabels: Record<RelationType, string> = {
  USES_MATERIAL: "использует материал",
  OPERATES_AT_CONDITION: "работает при условии",
  PRODUCES_OUTPUT: "производит результат",
  DESCRIBED_IN: "описано в",
  VALIDATED_BY: "подтверждено",
  CONTRADICTS: "противоречит",
  AUTHORED_BY: "автор",
  IN_DOMAIN: "в дисциплине",
  SHOWED: "показал",
  MEASURES: "измеряет",
  HAS_PARAM: "имеет параметр",
};

/** Метки для `Filters.geo` — нормализованные значения из API.md. */
export const geoOptions: { value: string; label: string }[] = [
  { value: "RU", label: "Россия / отечественная" },
  { value: "WORLD", label: "Зарубежная / мировая" },
  { value: "Kazakhstan", label: "Казахстан" },
  { value: "China", label: "Китай" },
  { value: "Canada", label: "Канада" },
];
