import { useState, type FormEvent } from "react";
import { api, setStoredToken } from "../api/client";
import type { User } from "../types/api";

interface Props {
  onAuthenticated: (user: User) => void;
}

export default function AuthPage({ onAuthenticated }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [studentCode, setStudentCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === "register") {
        await api.register({
          email,
          full_name: fullName,
          password,
          student_code: studentCode || undefined,
        });
      }
      const { access_token } = await api.login({ email, password });
      setStoredToken(access_token);
      const user = await api.me();
      onAuthenticated(user);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Đăng nhập thất bại.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-container">
      <div className="card">
        <h1 style={{ margin: "0 0 0.25rem", color: "var(--brand)" }}>Otter Assistant</h1>
        <p className="muted" style={{ marginTop: 0 }}>
          Your schedule. Your tasks. Your Otter. — UTH Academic Dashboard
        </p>

        {error && <div className="alert alert-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          {mode === "register" && (
            <>
              <div className="form-row">
                <label>Họ và tên</label>
                <input
                  className="input"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  required
                />
              </div>
              <div className="form-row">
                <label>Mã số sinh viên (tuỳ chọn)</label>
                <input
                  className="input"
                  value={studentCode}
                  onChange={(e) => setStudentCode(e.target.value)}
                />
              </div>
            </>
          )}
          <div className="form-row">
            <label>Email</label>
            <input
              className="input"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="form-row">
            <label>Mật khẩu</label>
            <input
              className="input"
              type="password"
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          <button className="btn btn-primary" style={{ width: "100%" }} disabled={loading}>
            {loading ? "Đang xử lý..." : mode === "login" ? "Đăng nhập" : "Tạo tài khoản"}
          </button>
        </form>

        <p className="muted" style={{ textAlign: "center", marginTop: "1rem" }}>
          {mode === "login" ? "Chưa có tài khoản? " : "Đã có tài khoản? "}
          <button
            type="button"
            className="btn"
            style={{ padding: "0.2rem 0.6rem" }}
            onClick={() => {
              setError(null);
              setMode(mode === "login" ? "register" : "login");
            }}
          >
            {mode === "login" ? "Đăng ký" : "Đăng nhập"}
          </button>
        </p>
      </div>
    </div>
  );
}
