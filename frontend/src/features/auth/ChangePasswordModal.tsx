import { useEffect, useRef, useState } from "react";
import { KeyRound, X } from "lucide-react";
import { authApi } from "../../api/auth-api";
import { useSessionStore } from "../../app/session-store";
import s from "../../styles/ui.module.css";

/** Модалка смены собственного пароля (POST /api/auth/change-password). */
export function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const { token } = useSessionStore();
  const [oldPassword, setOld] = useState(""); const [newPassword, setNew] = useState(""); const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null); const [done, setDone] = useState(false); const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLFormElement>(null);
  useEffect(() => { const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); }; document.addEventListener("keydown", k); return () => document.removeEventListener("keydown", k); }, [onClose]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    if (newPassword.length < 6) return setError("Новый пароль — минимум 6 символов");
    if (newPassword !== repeat) return setError("Пароли не совпадают");
    setBusy(true); setError(null);
    try { await authApi.changePassword(token, oldPassword, newPassword); setDone(true); setTimeout(onClose, 1200); }
    catch (err) { setError(err instanceof Error ? err.message : "Не удалось сменить пароль"); }
    finally { setBusy(false); }
  }

  return <div className={s.modalBackdrop} role="presentation" onClick={onClose}>
    <form ref={ref} className={s.modal} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
      <header><div><span className={s.modalIcon}><KeyRound/></span><div><h2>Сменить пароль</h2><p>Задайте новый пароль для входа</p></div></div><button type="button" onClick={onClose} aria-label="Закрыть"><X/></button></header>
      <div className={s.modalBody} style={{ display: "grid", gap: 12 }}>
        <label style={pwLabel}>Текущий пароль<input style={pwInput} type="password" value={oldPassword} onChange={(e) => setOld(e.target.value)} autoFocus required/></label>
        <label style={pwLabel}>Новый пароль (≥6)<input style={pwInput} type="password" value={newPassword} onChange={(e) => setNew(e.target.value)} required/></label>
        <label style={pwLabel}>Повторите новый<input style={pwInput} type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} required/></label>
        {error && <p className={s.formError}>{error}</p>}
        {done && <p style={{ color: "#16a34a", margin: 0, fontSize: 13 }}>Пароль изменён ✓</p>}
      </div>
      <footer><button type="button" className={s.ghostButton} onClick={onClose}>Отмена</button><button type="submit" className={s.primaryButton} disabled={busy || done}>{busy ? "Сохраняем…" : "Сменить"}</button></footer>
    </form>
  </div>;
}

const pwLabel: React.CSSProperties = { display: "grid", gap: 5, color: "#475569", fontSize: 12, fontWeight: 600 };
const pwInput: React.CSSProperties = { padding: "9px 11px", borderRadius: 8, border: "1px solid #cbd5e1", fontSize: 14 };
