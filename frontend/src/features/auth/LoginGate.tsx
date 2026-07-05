import { useState, type ReactNode } from "react";
import { Sparkles } from "lucide-react";
import { isMockMode } from "../../api/api-provider";
import { authApi } from "../../api/auth-api";
import { useSessionStore } from "../../app/session-store";

/**
 * Ворота аутентификации. В mock-режиме (демо) пропускает без логина.
 * В http-режиме требует вход, если нет валидного токена.
 */
export function LoginGate({ children }: { children: ReactNode }) {
  const { token, setAuth } = useSessionStore();
  if (isMockMode || token) return <>{children}</>;
  return <LoginScreen onLogin={setAuth} />;
}

function LoginScreen({ onLogin }: { onLogin: (t: string, u: string, r: never) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await authApi.login(username.trim(), password);
      onLogin(res.token, res.user.username, res.user.role as never);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка входа");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={wrap}>
      <form onSubmit={submit} style={card}>
        <div style={brand}>
          <span style={mark}><Sparkles size={22} /></span>
          <div><b style={{ fontSize: 18 }}>Научный клубок</b><br />
            <small style={{ color: "#64748b" }}>Вход в систему</small></div>
        </div>
        <label style={label}>Логин
          <input style={input} value={username} autoFocus
            onChange={(e) => setUsername(e.target.value)} placeholder="Ваш логин" />
        </label>
        <label style={label}>Пароль
          <input style={input} type="password" value={password}
            onChange={(e) => setPassword(e.target.value)} placeholder="••••••" />
        </label>
        {error && <div style={errBox}>{error}</div>}
        <button style={{ ...btn, opacity: busy ? 0.6 : 1 }} disabled={busy}>
          {busy ? "Вход…" : "Войти"}
        </button>
      </form>
    </div>
  );
}

const wrap: React.CSSProperties = {
  minHeight: "100vh", display: "grid", placeItems: "center",
  background: "linear-gradient(135deg,#0a192f,#112240)",
};
const card: React.CSSProperties = {
  width: 340, display: "flex", flexDirection: "column", gap: 14,
  padding: 32, borderRadius: 16, background: "#fff",
  boxShadow: "0 20px 60px rgba(0,0,0,.35)",
};
const brand: React.CSSProperties = { display: "flex", gap: 12, alignItems: "center", marginBottom: 6 };
const mark: React.CSSProperties = {
  width: 40, height: 40, borderRadius: 12, background: "#0a192f", color: "#64ffda",
  display: "grid", placeItems: "center",
};
const label: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 6, fontSize: 13, color: "#334155" };
const input: React.CSSProperties = {
  padding: "10px 12px", borderRadius: 8, border: "1px solid #cbd5e1", fontSize: 15,
};
const btn: React.CSSProperties = {
  padding: "11px 12px", borderRadius: 8, border: "none", background: "#0a66c2",
  color: "#fff", fontWeight: 600, fontSize: 15, cursor: "pointer",
};
const errBox: React.CSSProperties = {
  padding: "8px 12px", borderRadius: 8, background: "#fee2e2", color: "#b91c1c", fontSize: 13,
};
