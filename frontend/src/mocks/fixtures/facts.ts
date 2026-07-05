import type { DocHit, Fact, FilterOptions, SearchResponse } from "../../domain/types";

/**
 * Демонстрационный корпус фактов и документов.
 * Значения сняты с тех же кейсов, что и прежние сценарии (католит/обессоливание/МПГ/шахтные воды),
 * но приведены к форме `Fact` из API-контракта (§1).
 */

interface DocMeta {
  doc_id: string;
  title: string;
  geo: "RU" | "WORLD";
  year: number | null;
  sensitivity: "public" | "internal" | "secret";
  domains: string[];
}

export const docs: DocMeta[] = [
  { doc_id: "cdd5b92b3ff84174", title: "Обзор технических решений в области электролитического производства никеля и меди", geo: "WORLD", year: 2025, sensitivity: "public", domains: ["гидрометаллургия"] },
  { doc_id: "b57f23eb9e13b736", title: "Особенности состава электролита в технологиях электроэкстракции", geo: "WORLD", year: 2003, sensitivity: "public", domains: ["гидрометаллургия"] },
  { doc_id: "6d48e64d934f8d7d", title: "Новая схема циркуляции растворов при электроэкстракции никеля", geo: "WORLD", year: 2013, sensitivity: "public", domains: ["гидрометаллургия"] },
  { doc_id: "4aff936f59da1880", title: "Очистка промышленных стоков цветной металлургии", geo: "WORLD", year: 2015, sensitivity: "public", domains: ["экология"] },
  { doc_id: "2eaead09cf74b4b2", title: "Замкнутая система водопользования с обратным осмосом", geo: "WORLD", year: 2010, sensitivity: "public", domains: ["экология", "переработка отходов"] },
  { doc_id: "2efcea298dad83ca", title: "Методы очистки шахтных вод", geo: "WORLD", year: 2025, sensitivity: "internal", domains: ["экология"] },
  { doc_id: "43f9157e458b127e", title: "Развитие автоклавной гидрометаллургии пирротиновых концентратов", geo: "RU", year: 2004, sensitivity: "internal", domains: ["гидрометаллургия", "экология"] },
  { doc_id: "a2f56b6b3438d22f", title: "Распределение Au, Ag и МПГ между медным/никелевым штейном и шлаком", geo: "WORLD", year: 2018, sensitivity: "public", domains: ["пирометаллургия"] },
  { doc_id: "80361f9171bda73b", title: "Влияние состава штейна на распределение благородных металлов", geo: "WORLD", year: null, sensitivity: "public", domains: ["пирометаллургия"] },
];

export const facts: Fact[] = [
  {
    canon: "электроэкстракция", metric: "скорость циркуляции католита", value_low: 20, value_high: 30, unit: null,
    phase: "католит", quote: "Скорость циркуляции обычно составляет 20–30 л/ч, регулирование разностью уровней католита и анолита.",
    doc_id: "cdd5b92b3ff84174", year: 2025, source: "pattern", track: "numeric", ref: "catholyte-flow",
    confidence: 0.88, extracted_at: "2026-07-04T00:08:16Z", sensitivity: "public",
  },
  {
    canon: "электроэкстракция", metric: "перенос ионов водорода", value_low: 0, value_high: 0, unit: null,
    phase: "католит/анолит", quote: "Перенос ионов водорода из анодного пространства в катодное зависит от скорости циркуляции.",
    doc_id: "b57f23eb9e13b736", year: 2003, source: "pattern", track: "search", ref: "catholyte-transport",
    confidence: 0.72, extracted_at: "2026-07-04T00:08:16Z", sensitivity: "public",
  },
  {
    canon: "электроэкстракция", metric: "контур циркуляции", value_low: 0, value_high: 0, unit: null,
    phase: "католит", quote: "Предложена новая схема: часть очищенного католита циркулирует в малом контуре.",
    doc_id: "6d48e64d934f8d7d", year: 2013, source: "pattern", track: "search", ref: "catholyte-loop",
    confidence: 0.68, extracted_at: "2026-07-04T00:08:16Z", sensitivity: "public",
  },
  {
    canon: "шахтная вода", metric: "сухой остаток", value_low: 1930, value_high: 1930, unit: "mg_L",
    phase: null, quote: "Сухой остаток — 1930 мг/л при нормативе не более 1000 мг/л.",
    doc_id: "4aff936f59da1880", year: 2015, source: "pattern", track: "numeric", ref: "dry-residue",
    confidence: 0.85, extracted_at: "2026-07-04T00:08:16Z", sensitivity: "public",
  },
  {
    canon: "шахтная вода", metric: "сульфат-ионы", value_low: 1200, value_high: 1200, unit: "mg_L",
    phase: null, quote: "Сульфат-ионы — 1200 мг/л, хлорид-ионы — 256 мг/л.",
    doc_id: "4aff936f59da1880", year: 2015, source: "pattern", track: "numeric", ref: "sulfate",
    confidence: 0.85, extracted_at: "2026-07-04T00:08:16Z", sensitivity: "public",
  },
  {
    canon: "обессоливание", metric: "разделение обратный осмос", value_low: 0, value_high: 0, unit: null,
    phase: null, quote: "В таблице приведён состав фильтрата и концентрата обратного осмоса.",
    doc_id: "2eaead09cf74b4b2", year: 2010, source: "pattern", track: "search", ref: "ro",
    confidence: 0.6, extracted_at: "2026-07-04T00:08:16Z", sensitivity: "public",
  },
  {
    canon: "глубинная закачка", metric: "закачка сернистых стоков", value_low: 0, value_high: 0, unit: null,
    phase: null, quote: "Технология усовершенствована; прекращена закачка сернистых стоков в подземные водоносные горизонты.",
    doc_id: "43f9157e458b127e", year: 2004, source: "manual", track: "search", ref: "mine-injection",
    confidence: 0.55, extracted_at: "2026-07-04T00:08:16Z", sensitivity: "internal",
  },
  {
    canon: "плавка", metric: "распределение МПГ", value_low: 0, value_high: 0, unit: null,
    phase: "штейн/шлак", quote: "Диапазон коэффициентов распределения благородных металлов увеличивается в ряду Ag→Au→Ru→Ir→Pt (Pd, Rh).",
    doc_id: "80361f9171bda73b", year: 2021, source: "pattern", track: "expert", ref: "pgm-order",
    confidence: 0.83, extracted_at: "2026-07-04T00:08:16Z", sensitivity: "public",
  },
  {
    canon: "золото", metric: "содержание", value_low: 2.77, value_high: 2.77, unit: "g_t",
    phase: "шлак", quote: "Содержание золота в шлаке составило 2.77 г/т.",
    doc_id: "80361f9171bda73b", year: 2021, source: "pattern", track: "numeric", ref: "au-slag",
    confidence: 0.84, extracted_at: "2026-07-04T00:08:16Z", sensitivity: "public",
  },
];

export const experts: { name: string; doc_ids: string[]; domains: string[] }[] = [
  { name: "Евграфова А. К.", doc_ids: ["a2f56b6b3438d22f", "80361f9171bda73b"], domains: ["пирометаллургия"] },
];

export const filterOptions: FilterOptions = {
  years: [2025, 2024, 2023, 2021, 2018, 2015, 2013, 2010, 2004, 2003],
  geos: ["RU", "WORLD"],
  materials: ["никель", "медь", "католит", "сульфат", "хлорид", "штейн", "шлак", "золото", "МПГ", "шахтная вода"],
  processes: ["электроэкстракция", "обессоливание", "плавка", "глубинная закачка", "обжиг"],
  confidence_levels: ["высокая", "средняя", "низкая"],
};

export function docById(id: string): DocMeta | undefined {
  return docs.find((d) => d.doc_id === id);
}

export function docHit(id: string, source: string): DocHit {
  return { doc_id: id, source };
}

interface Scenario {
  match: RegExp;
  intent: SearchResponse["intent"];
  selectDocIds: string[];
  answerMd: (ids: string[]) => string;
}

const scenarios: Scenario[] = [
  {
    match: /католит|циркуляц.*электролит|электроэкстракц.*никел/i,
    intent: "numeric",
    selectDocIds: ["cdd5b92b3ff84174", "b57f23eb9e13b736", "6d48e64d934f8d7d"],
    answerMd: (ids) =>
      `## Католитная циркуляция при электроэкстракции\n\nТипичная **скорость циркуляции католита** в проточной катодной диафрагме — **20–30 л/ч** [${ids[0].slice(0, 4)}]; регулирование выполняется разностью уровней католита и анолита. Универсальное оптимум-значение нельзя переносить на любую ванну без проверки геометрии ячейки, плотности тока и состава электролита.`,
  },
  {
    match: /обессол|сухой остаток|сульфат.*хлорид|обратный осмос/i,
    intent: "numeric",
    selectDocIds: ["4aff936f59da1880", "2eaead09cf74b4b2"],
    answerMd: (ids) =>
      `## Обессоливание шахтной воды\n\nНайденный кейс (сухой остаток **1930 мг/л**, сульфаты **1200 мг/л** [${ids[0].slice(0, 4)}]) не удовлетворяет цели ≤1000 мг/л без дополнительной деминерализации. Релевантная стадия — **обратный осмос** [${ids[1].slice(0, 4)}] в схеме замкнутого водооборота.`,
  },
  {
    match: /мпг|штейн.*шлак|au.*ag|благородн.*металл|золото/i,
    intent: "expert",
    selectDocIds: ["80361f9171bda73b", "a2f56b6b3438d22f"],
    answerMd: (ids) =>
      `## Распределение Au, Ag и МПГ\n\nКорпус подтверждает преимущественное концентрирование благородных металлов в штейновой фазе [${ids[0].slice(0, 4)}]. Коэффициенты распределения растут в ряду Ag→Au→Ru→Ir→Pt (Pd, Rh). Профильный обзор датирован 2018 г. [${ids[1].slice(0, 4)}], поэтому фильтр «за последние 5 лет» покрыт недостаточно.`,
  },
  {
    match: /закач.*вод|шахтн.*вод.*горизонт|глубин.*горизонт|шахтн.*вод/i,
    intent: "search",
    selectDocIds: ["43f9157e458b127e", "2efcea298dad83ca"],
    answerMd: (ids) =>
      `## Шахтные воды и глубинная закачка\n\nРоссийский кейс [${ids[0].slice(0, 4)}] фиксирует прекращение закачки сернистых стоков в водоносные горизонты после модернизации. Сопоставимые CAPEX/OPEX по России и зарубежью в корпусе отсутствуют — это пробел, а не числовое противоречие.`,
  },
];

export function resolveScenario(query: string): Scenario | undefined {
  return scenarios.find((s) => s.match.test(query));
}

export function emptyDocs(ids: string[]): DocHit[] {
  return ids.map((id) => docHit(id, docById(id)?.title ? "pattern" : "unknown"));
}