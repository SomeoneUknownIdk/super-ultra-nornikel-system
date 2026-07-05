import { useEffect, useState } from "react";
import { authApi, type AuthUser } from "../../api/auth-api";
import { useSessionStore } from "../../app/session-store";
import type { Role } from "../../domain/types";
import { roleLabels } from "../../domain/labels";

const ROLES: Role[] = ["researcher", "analyst", "project_lead", "admin", "external_partner"];

/** Управление пользователями (админ): создание, роль, пароль, блокировка, удаление. */
export function UsersPage() {
  const { token, username: me } = useSessionStore();
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ username: "", password: "", role: "researcher" as Role, full_name: "" });

  async function refresh() {
    if (!token) return;
    try { setUsers(await authApi.listUsers(token)); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : "Ошибка"); }
  }
  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [token]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    try {
      await authApi.createUser(token, form);
      setForm({ username: "", password: "", role: "researcher", full_name: "" });
      refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Ошибка"); }
  }

  async function setRoleOf(u: AuthUser, role: Role) {
    if (!token) return;
    try { await authApi.updateUser(token, u.username, { role }); refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Ошибка"); }
  }
  async function toggle(u: AuthUser) {
    if (!token) return;
    try { await authApi.updateUser(token, u.username, { active: !(u.active ?? true) }); refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Ошибка"); }
  }
  async function remove(u: AuthUser) {
    if (!token || u.username === me) return;
    if (!confirm(`Удалить пользователя ${u.username}?`)) return;
    try { await authApi.deleteUser(token, u.username); refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Ошибка"); }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 880 }}>
      <div>
        <h1 style={{ margin: 0 }}>Пользователи</h1>
        <p style={{ color: "#64748b", marginTop: 4 }}>
          Создание учётных записей, роли и доступ. Пароли хранятся в bcrypt.
        </p>
      </div>
      {error && <div style={err}>{error}</div>}

      <form onSubmit={create} style={createBox}>
        <b style={{ gridColumn: "1 / -1" }}>Новый пользователь</b>
        <input style={inp} placeholder="логин" value={form.username}
          onChange={(e) => setForm({ ...form, username: e.target.value })} required />
        <input style={inp} placeholder="пароль (≥6)" type="password" value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })} required />
        <input style={inp} placeholder="ФИО" value={form.full_name}
          onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
        <select style={inp} value={form.role}
          onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
          {ROLES.map((r) => <option key={r} value={r}>{roleLabels[r]}</option>)}
        </select>
        <button style={btn}>Создать</button>
      </form>

      <div style={{ overflowX: "auto", width: "100%" }}>
      <table style={table}>
        <thead><tr>
          <th style={th}>Логин</th><th style={th}>ФИО</th><th style={th}>Роль</th>
          <th style={th}>Статус</th><th style={th}>Действия</th>
        </tr></thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.username}>
              <td style={td}><b>{u.username}</b>{u.username === me && <span style={youBadge}>вы</span>}</td>
              <td style={td}>{u.full_name || "—"}</td>
              <td style={td}>
                <select value={u.role} onChange={(e) => setRoleOf(u, e.target.value as Role)}
                  disabled={u.username === me} style={inpSm}>
                  {ROLES.map((r) => <option key={r} value={r}>{roleLabels[r]}</option>)}
                </select>
              </td>
              <td style={td}>
                <span style={{ color: (u.active ?? true) ? "#16a34a" : "#dc2626" }}>
                  {(u.active ?? true) ? "активен" : "заблокирован"}
                </span>
              </td>
              <td style={td}>
                <button style={linkBtn} onClick={() => toggle(u)} disabled={u.username === me}>
                  {(u.active ?? true) ? "заблокировать" : "разблокировать"}
                </button>
                <button style={{ ...linkBtn, color: "#dc2626" }} onClick={() => remove(u)}
                  disabled={u.username === me}>удалить</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}

const err: React.CSSProperties = { padding: "8px 12px", borderRadius: 8, background: "#fee2e2", color: "#b91c1c" };
const createBox: React.CSSProperties = {
  // auto-fit: на широком экране в ряд, на узком — переносится (без переполнения)
  display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
  gap: 8, alignItems: "center", maxWidth: "100%",
  padding: 16, borderRadius: 12, background: "#f8fafc", border: "1px solid #e2e8f0",
};
const inp: React.CSSProperties = { padding: "8px 10px", borderRadius: 8, border: "1px solid #cbd5e1", fontSize: 14 };
const inpSm: React.CSSProperties = { ...inp, padding: "4px 6px", fontSize: 13 };
const btn: React.CSSProperties = { padding: "8px 14px", borderRadius: 8, border: "none", background: "#0a66c2", color: "#fff", fontWeight: 600, cursor: "pointer" };
const table: React.CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: 14 };
const th: React.CSSProperties = { textAlign: "left", padding: "8px 10px", borderBottom: "2px solid #e2e8f0", color: "#475569" };
const td: React.CSSProperties = { padding: "8px 10px", borderBottom: "1px solid #f1f5f9" };
const linkBtn: React.CSSProperties = { background: "none", border: "none", color: "#0a66c2", cursor: "pointer", marginRight: 10, fontSize: 13 };
const youBadge: React.CSSProperties = { marginLeft: 6, fontSize: 11, color: "#0a66c2", background: "#e0f2fe", padding: "1px 6px", borderRadius: 6 };
