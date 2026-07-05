import { BarChart3, BookOpen, CircleHelp, GitFork, Globe, KeyRound, LogOut, Menu, Search, ShieldCheck, Sparkles, Users, X } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useRef, useState } from "react";
import { isMockMode } from "../api/api-provider";
import { roleLabels } from "../domain/labels";
import { useSessionStore } from "./session-store";
import { useDismiss } from "./use-dismiss";
import { ChangePasswordModal } from "../features/auth/ChangePasswordModal";
import s from "../styles/ui.module.css";

const nav = [
  { to: "/", label: "Поиск", icon: Search, end: true, roles: null as null | string[], mobileHidden: false },
  { to: "/graph", label: "Граф", icon: GitFork, end: false, roles: null, mobileHidden: false },
  { to: "/sources", label: "Источники", icon: BookOpen, end: false, roles: null, mobileHidden: false },
  { to: "/external", label: "Внешние", icon: Globe, end: false, roles: null, mobileHidden: false },
  { to: "/quality", label: "Качество", icon: ShieldCheck, end: false, roles: null, mobileHidden: false },
  { to: "/analytics", label: "Аналитика", icon: BarChart3, end: false, roles: ["project_lead", "admin"], mobileHidden: false },
  // «Пользователи» (admin) — только в десктопном сайдбаре; на мобиле таб-бар из 5,
  // управление доступно из профиль-меню (длинная метка не влезает в 6-й таб).
  { to: "/users", label: "Пользователи", icon: Users, end: false, roles: ["admin"], mobileHidden: true },
];
const roles = Object.keys(roleLabels) as (keyof typeof roleLabels)[];

export function App() {
  const [profileOpen, setProfileOpen] = useState(false); const [helpOpen, setHelpOpen] = useState(false); const [pwOpen, setPwOpen] = useState(false);
  const { role, setRole, reset, token, username, logout } = useSessionStore();
  const profileRef = useRef<HTMLDivElement>(null);
  useDismiss(profileOpen, () => setProfileOpen(false), profileRef);  // закрытие по клику вне / Escape
  const visibleNav = nav.filter((item) => !item.roles || item.roles.includes(role));
  const mobileNav = visibleNav.filter((item) => !item.mobileHidden);
  return <div className={s.appShell}>
    <aside className={s.sidebar}>
      <div className={s.brand}><span className={s.brandMark}><Sparkles size={20} /></span><span><b>Научный клубок</b><small>R&D knowledge map</small></span></div>
      <nav className={s.desktopNav} aria-label="Основная навигация">{visibleNav.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} className={({ isActive }) => `${s.navItem} ${isActive ? s.navActive : ""}`}><Icon size={19}/><span>{label}</span></NavLink>)}</nav>
      <div className={s.sidebarFooter}><div className={s.statusLine}><span className={s.statusDot}/><span>{isMockMode ? "Демо-данные" : "API подключён"}</span></div><button className={s.helpButton} onClick={() => setHelpOpen(true)}><CircleHelp size={18}/>Как пользоваться</button></div>
    </aside>
    <div className={s.workspace}>
      <header className={s.topbar}>
        <div className={s.mobileBrand}><span className={s.brandMark}><Sparkles size={18}/></span><b>Научный клубок</b></div>
        <div className={s.topbarSpacer}/>
        {isMockMode && <span className={s.demoBadge}>ДЕМО-РЕЖИМ</span>}
        <div className={s.profileWrap} ref={profileRef}>
          <button className={s.profileButton} onClick={() => setProfileOpen((v) => !v)} aria-expanded={profileOpen}><span className={s.avatar}>{(username ?? "НИ").slice(0, 2).toUpperCase()}</span><span className={s.profileText}><b>{username ?? roleLabels[role]}</b><small>{token ? roleLabels[role] : "Демонстрационная роль"}</small></span><Menu size={17}/></button>
          {profileOpen && <div className={s.profileMenu} role="menu">
            {token
              ? <><p>{username} · {roleLabels[role]}</p>{role === "admin" && <><hr/><NavLink to="/users" onClick={() => setProfileOpen(false)}><Users size={16}/>Пользователи</NavLink></>}<hr/><button onClick={() => { setPwOpen(true); setProfileOpen(false); }}><KeyRound size={16}/>Сменить пароль</button><button onClick={() => { logout(); setProfileOpen(false); }}><LogOut size={16}/>Выйти</button></>
              : <><p>Роль в демо-режиме</p>{roles.map((item) => <button key={item} className={item === role ? s.menuSelected : ""} onClick={() => { setRole(item); setProfileOpen(false); }}>{roleLabels[item]}{item === role && <span>✓</span>}</button>)}<hr/><button onClick={reset}><LogOut size={16}/>Сбросить демо-данные</button></>}
          </div>}
        </div>
      </header>
      <main className={s.main}><Outlet/></main>
    </div>
    <nav className={s.mobileNav} aria-label="Мобильная навигация" style={{ gridTemplateColumns: `repeat(${mobileNav.length}, 1fr)` }}>{mobileNav.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} className={({ isActive }) => `${s.mobileNavItem} ${isActive ? s.mobileNavActive : ""}`}><Icon size={20}/><span>{label}</span></NavLink>)}</nav>
    {pwOpen && token && <ChangePasswordModal onClose={() => setPwOpen(false)}/>}
    {helpOpen && <HelpModal onClose={() => setHelpOpen(false)}/>}
  </div>;
}

function HelpModal({ onClose }: { onClose: () => void }) {
  const steps = [
    ["Поиск", "Задайте вопрос обычным языком — система найдёт факты с цитатами из источников. Фильтры по географии, материалу, достоверности."],
    ["Граф", "Введите материал или процесс — исследуйте связи. Клик по узлу открывает детали и число источников, глубина 1–3."],
    ["Источники", "Все документы корпуса с метаданными и фактами. Добавить новый источник — кнопкой «Добавить источники»."],
    ["Качество", "Автоматически найденные противоречия между источниками (Россия vs мир, метод vs метод)."],
    ["Аналитика", "Дашборд руководителя: покрытие корпуса, география, домены, зоны риска (роли «Руководитель»/«Админ»)."],
  ];
  return <div className={s.modalBackdrop} role="presentation" onClick={onClose}>
    <section className={s.modal} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
      <header><div><span className={s.modalIcon}><CircleHelp/></span><div><h2>Как пользоваться</h2><p>Пять разделов «Научного клубка»</p></div></div><button type="button" onClick={onClose} aria-label="Закрыть"><X/></button></header>
      <div className={s.modalBody} style={{ display: "grid", gap: 12 }}>
        {steps.map(([title, text]) => <div key={title} style={{ display: "grid", gap: 3 }}><b style={{ color: "#0a66c2", fontSize: 13 }}>{title}</b><span style={{ color: "#475569", fontSize: 13, lineHeight: 1.5 }}>{text}</span></div>)}
      </div>
      <footer><button type="button" className={s.primaryButton} onClick={onClose}>Понятно</button></footer>
    </section>
  </div>;
}
