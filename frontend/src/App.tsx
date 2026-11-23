import { useEffect, useState } from "react";
import { api, getStoredToken, setStoredToken } from "./api/client";
import AssignmentsPage from "./pages/AssignmentsPage";
import AuthPage from "./pages/AuthPage";
import DashboardPage from "./pages/DashboardPage";
import IntegrationsPage from "./pages/IntegrationsPage";
import RemindersPage from "./pages/RemindersPage";
import SchedulePage from "./pages/SchedulePage";
import SettingsPage from "./pages/SettingsPage";
import SubjectsPage from "./pages/SubjectsPage";
import type { User } from "./types/api";

type PageKey =
  | "dashboard"
  | "schedule"
  | "subjects"
  | "assignments"
  | "reminders"
  | "integrations"
  | "settings";

const NAV_ITEMS: Array<{ key: PageKey; label: string }> = [
  { key: "dashboard", label: "Dashboard" },
  { key: "schedule", label: "Thời khóa biểu" },
  { key: "subjects", label: "Học phần" },
  { key: "assignments", label: "Bài tập & Deadline" },
  { key: "reminders", label: "Nhắc nhở" },
  { key: "integrations", label: "Tích hợp" },
  { key: "settings", label: "Cài đặt" },
];

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [page, setPage] = useState<PageKey>("dashboard");

  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      setCheckingAuth(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => setStoredToken(null))
      .finally(() => setCheckingAuth(false));
  }, []);

  if (checkingAuth) {
    return (
      <div className="auth-container">
        <div className="card">Đang kiểm tra phiên đăng nhập...</div>
      </div>
    );
  }

  if (!user) {
    return <AuthPage onAuthenticated={setUser} />;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <h1>Otter Assistant</h1>
          <p>Your schedule. Your tasks. Your Otter.</p>
        </div>
        <nav className="nav-list">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              className={`nav-btn ${page === item.key ? "active" : ""}`}
              onClick={() => setPage(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div><strong>{user.full_name}</strong></div>
          <div style={{ color: "#94a3b8", fontSize: "0.78rem", marginBottom: "0.6rem" }}>
            {user.email}
          </div>
          <button
            className="btn"
            style={{ width: "100%" }}
            onClick={() => {
              setStoredToken(null);
              setUser(null);
            }}
          >
            Đăng xuất
          </button>
        </div>
      </aside>

      <main className="main-content">
        {page === "dashboard" && <DashboardPage />}
        {page === "schedule" && <SchedulePage />}
        {page === "subjects" && <SubjectsPage />}
        {page === "assignments" && <AssignmentsPage />}
        {page === "reminders" && <RemindersPage />}
        {page === "integrations" && <IntegrationsPage />}
        {page === "settings" && <SettingsPage user={user} />}
      </main>
    </div>
  );
}
