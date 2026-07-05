import {
  type CompareResponse,
  type CoverageDomain,
  type CoverageGeo,
  type CoverageYear,
  type DashboardActivity,
  type DashboardExpert,
  type DashboardSummary,
  type RiskZones,
} from "../../domain/types";

/** GET /api/dashboard/summary — KPI-объект (API.md §5). */
export const dashboardSummary: DashboardSummary = {
  docs: 1288,
  facts: 21198,
  experts: 148,
  domains: 5,
  contradictions: 272,
  ru: 514,
  world: 755,
  geo_unknown: 0,
  ru_share: 0.4,
  world_share: 0.59,
  docs_with_facts: 144,
  fact_coverage: 0.112,
};

export const coverageByDomain: CoverageDomain[] = [
  { domain: "гидрометаллургия", documents: 412, facts: 7180, experts: 51 },
  { domain: "пирометаллургия", documents: 301, facts: 5240, experts: 38 },
  { domain: "экология", documents: 244, facts: 4112, experts: 29 },
  { domain: "переработка отходов", documents: 198, facts: 3310, experts: 19 },
  { domain: "горное дело", documents: 133, facts: 1356, experts: 11 },
];

export const coverageByYear: CoverageYear[] = [
  { year: 2019, documents: 188, facts: 2710 },
  { year: 2020, documents: 201, facts: 3022 },
  { year: 2021, documents: 246, facts: 4014 },
  { year: 2022, documents: 272, facts: 4691 },
  { year: 2023, documents: 305, facts: 5230 },
  { year: 2024, documents: 372, facts: 6041 },
  { year: 2025, documents: 404, facts: 6890 },
];

export const coverageByGeo: CoverageGeo[] = [
  { geo: "WORLD", documents: 755, facts: 12910 },
  { geo: "RU", documents: 514, facts: 8264 },
  { geo: "Kazakhstan", documents: 12, facts: 18 },
  { geo: "China", documents: 7, facts: 6 },
];

export const riskZones: RiskZones = {
  low_sources: [
    { entity: "CO", type: "Material", sources: 1 },
    { entity: "обжиг", type: "Process", sources: 1 },
  ],
  contradictions: [],
  only_ru: [{ entity: "глубинная закачка", type: "Process", sources: 1 }],
  only_world: [{ entity: "кучное выщелачивание", type: "Process", sources: 14 }],
};

export const dashboardActivity: DashboardActivity[] = [
  { doc_id: "cdd5b92b3ff84174", year: 2025, geo: "WORLD", facts: 18, experts: 0, last_extracted: "2026-07-04T00:08:16Z" },
  { doc_id: "80361f9171bda73b", year: 2021, geo: "WORLD", facts: 13, experts: 1, last_extracted: "2026-07-04T00:08:16Z" },
  { doc_id: "4aff936f59da1880", year: 2015, geo: "WORLD", facts: 22, experts: 0, last_extracted: "2026-07-04T00:08:16Z" },
  { doc_id: "43f9157e458b127e", year: 2004, geo: "RU", facts: 9, experts: 0, last_extracted: "2026-07-04T00:08:16Z" },
];

export const dashboardExperts: DashboardExpert[] = [
  { expert: "Евграфова А. К.", documents: 4, domains: 1, domain_list: ["пирометаллургия"] },
  { expert: "Сидоров П. Н.", documents: 7, domains: 2, domain_list: ["гидрометаллургия", "экология"] },
  { expert: "Минин Р. О.", documents: 3, domains: 1, domain_list: ["переработка отходов"] },
];

export function compareTechnologies(processes: string[]): CompareResponse {
  const rows = processes.map((process) => ({
    process,
    efficiency_pct: { min: 15.0, max: 98.0, unit: "pct" as const, unit_ru: "%", samples: 11 },
    energy: null,
    temperature_c: { min: 0.0, max: 1200.0, unit: "degC" as const, unit_ru: "°C", samples: 8 },
    cold_climate: process === "глубинная закачка",
    ecology: { min: 0.0, max: 0.6, unit: "pct" as const, unit_ru: "%", samples: 4 },
    capex: null,
  }));
  return {
    axes: ["efficiency_pct", "energy", "temperature_c", "cold_climate", "ecology", "capex"],
    meta: { unavailable: ["energy", "capex"] },
    rows,
  };
}