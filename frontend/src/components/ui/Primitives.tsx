import type { PropsWithChildren, ReactNode } from "react";
import { AlertCircle, Inbox, LoaderCircle } from "lucide-react";
import s from "../../styles/ui.module.css";

export function Badge({ children, tone = "neutral" }: PropsWithChildren<{ tone?: "neutral" | "blue" | "green" | "amber" | "red" }>) { return <span className={`${s.badge} ${s[`badge_${tone}`]}`}>{children}</span>; }
export function Stars({ value }: { value: number }) { return <span className={s.stars} aria-label={`Доверие: ${value} из 5`}>{"★".repeat(value)}<span>{"★".repeat(5 - value)}</span></span>; }
export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description: string; actions?: ReactNode }) { return <header className={s.pageHeader}><div>{eyebrow && <div className={s.eyebrow}>{eyebrow}</div>}<h1>{title}</h1><p>{description}</p></div>{actions && <div className={s.headerActions}>{actions}</div>}</header>; }
export function LoadingBlock({ label = "Загружаем данные" }: { label?: string }) { return <div className={s.loadingBlock}><LoaderCircle className={s.spin}/><span>{label}</span></div>; }
export function EmptyState({ title, text, action }: { title: string; text: string; action?: ReactNode }) { return <div className={s.emptyState}><Inbox/><h3>{title}</h3><p>{text}</p>{action}</div>; }
export function ErrorState({ message }: { message: string }) { return <div className={s.errorState}><AlertCircle/><div><b>Не удалось загрузить данные</b><p>{message}</p></div></div>; }
