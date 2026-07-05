import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BookOpenText, Database, Network, TriangleAlert, Users } from "lucide-react";
import { api } from "../../api/api-provider";
import { geographyLabels } from "../../domain/labels";
import { useSessionStore } from "../../app/session-store";
import { ErrorState, LoadingBlock, PageHeader } from "../../components/ui/Primitives";
import s from "../../styles/ui.module.css";

const palette = ["#2563eb", "#0f766e", "#d97706", "#7c3aed", "#64748b"];

export function AnalyticsPage() {
  const { role } = useSessionStore();
  const summary = useQuery({ queryKey: ["dashboard", "summary"], queryFn: () => api.dashboardSummary() });
  const byYear = useQuery({ queryKey: ["dashboard", "coverage", "year"], queryFn: () => api.dashboardCoverageYear() });
  const byDomain = useQuery({ queryKey: ["dashboard", "coverage", "domain"], queryFn: () => api.dashboardCoverageDomain() });
  const byGeo = useQuery({ queryKey: ["dashboard", "coverage", "geo"], queryFn: () => api.dashboardCoverageGeo() });
  const risks = useQuery({ queryKey: ["dashboard", "risks"], queryFn: () => api.dashboardRisks() });

  if (role !== "project_lead" && role !== "admin") {
    return <div><PageHeader eyebrow="ДОСТУП" title="Аналитика" description="Дашборд руководителя"/><div className={s.emptyState}><TriangleAlert/><h3>Недостаточно прав</h3><p>Раздел доступен ролям «Руководитель проекта» и «Администратор» (API.md §5).</p></div></div>;
  }
  const s1 = summary.data; const loading = summary.isLoading; const error = summary.error;
  return <div><PageHeader eyebrow="ПОКРЫТИЕ КОРПУСА" title="Аналитика" description="Масштаб базы знаний, распределение источников и зоны риска."/>
    {loading ? <LoadingBlock/> : error ? <ErrorState message={error.message}/> : s1 && <>
      <div className={s.metricGrid}>
        <Metric icon={<BookOpenText/>} value={s1.docs.toLocaleString("ru-RU")} label="Документов"/>
        <Metric icon={<Database/>} value={compact(s1.facts)} label="Фактов"/>
        <Metric icon={<Network/>} value={s1.domains.toLocaleString("ru-RU")} label="Дисциплин"/>
        <Metric icon={<Users/>} value={s1.experts} label="Экспертов"/>
        <Metric icon={<TriangleAlert/>} value={s1.contradictions} label="Противоречий" tone="red"/>
        <Metric icon={<BookOpenText/>} value={`${Math.round(s1.fact_coverage * 100)}%`} label="Покрытие фактами" tone="amber"/>
        <Metric icon={<Database/>} value={s1.ru.toLocaleString("ru-RU")} label="Российских"/>
        <Metric icon={<Database/>} value={s1.world.toLocaleString("ru-RU")} label="Зарубежных"/>
        {s1.geo_unknown > 0 && <Metric icon={<Database/>} value={s1.geo_unknown.toLocaleString("ru-RU")} label="Без геолокации"/>}
      </div>
      <div className={s.chartGrid}>
        <section className={`${s.panel} ${s.chartWide}`}><ChartTitle title="Рост корпуса" subtitle="Документы по годам"/><div className={s.chart}><ResponsiveContainer width="100%" height="100%"><AreaChart data={(byYear.data ?? []).filter((d) => d.year != null)}><CartesianGrid stroke="#e7eaf0" vertical={false}/><XAxis dataKey="year" tickLine={false} axisLine={false}/><YAxis tickLine={false} axisLine={false}/><Tooltip/><Area type="monotone" dataKey="documents" stroke="#2563eb" strokeWidth={3} fill="#dbe7ff"/></AreaChart></ResponsiveContainer></div></section>
        <section className={s.panel}><ChartTitle title="География" subtitle="Практики по регионам"/><div className={s.donutWrap}><ResponsiveContainer width="100%" height={220}><PieChart><Pie data={byGeo.data ?? []} dataKey="documents" nameKey="geo" innerRadius={62} outerRadius={88} paddingAngle={3}>{(byGeo.data ?? []).map((_, index) => <Cell key={index} fill={palette[index % palette.length]}/>)}</Pie><Tooltip/></PieChart></ResponsiveContainer><div className={s.donutLegend}>{(byGeo.data ?? []).map((item, index) => <Link key={item.geo} to={`/sources?geo=${encodeURIComponent(item.geo)}`} style={{ color: "inherit", textDecoration: "none" }}><i style={{ background: palette[index % palette.length] }}/>{geographyLabels[item.geo] ?? item.geo}<b>{item.documents}</b></Link>)}</div></div></section>
        <section className={s.panel}><ChartTitle title="Домены" subtitle="Доля структурированных знаний"/><div className={s.chart}><ResponsiveContainer width="100%" height="100%"><BarChart data={(byDomain.data ?? []).filter((d) => d.documents > 0)} layout="vertical" margin={{ left: 20 }}><CartesianGrid stroke="#e7eaf0" horizontal={false}/><XAxis type="number" hide/><YAxis dataKey="domain" type="category" width={130} tickLine={false} axisLine={false}/><Tooltip/><Bar dataKey="documents" radius={[0, 6, 6, 0]} fill="#0f766e" barSize={18}/></BarChart></ResponsiveContainer></div></section>
        <section className={s.panel}><ChartTitle title="Зоны риска" subtitle="Низкое покрытие и односторонняя география"/><div className={s.sourceBars}>{risks.data?.low_sources.map((item) => <Link key={item.entity} to={`/?entity=${encodeURIComponent(item.entity)}`} style={{ color: "inherit", textDecoration: "none", display: "block" }}><span>{item.entity}<b>{item.sources} ист.</b></span><i><em style={{ width: `${Math.min(100, item.sources * 25)}%` }}/></i></Link>)}</div></section>
      </div>
    </>}
  </div>;
}

function Metric({ icon, value, label, tone = "blue" }: { icon: React.ReactNode; value: string | number; label: string; tone?: string }) { return <div className={s.metricCard}><span className={s[`metric_${tone}`]}>{icon}</span><div><strong>{value}</strong><small>{label}</small></div></div>; }
function ChartTitle({ title, subtitle }: { title: string; subtitle: string }) { return <div className={s.chartTitle}><h3>{title}</h3><p>{subtitle}</p></div>; }
function compact(value: number) { return value > 999_999 ? `${(value / 1_000_000).toFixed(1)} млн` : value > 999 ? `${(value / 1000).toFixed(1)} тыс.` : String(value); }
