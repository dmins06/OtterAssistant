import { useEffect, useState, type FormEvent } from "react";
import { api, formatLocalDateTime } from "../api/client";
import type {
  Assignment,
  LMSStatus,
  PortalStatus,
  ScheduleItem,
  TelegramStatus,
} from "../types/api";

interface ChatTurn {
  role: "user" | "assistant";
  text: string;
}

function statusBadgeClass(status: string): string {
  if (status === "AUTHENTICATED") return "badge badge-ok";
  if (status === "REAUTH_REQUIRED" || status === "SESSION_EXPIRED" || status === "ERROR") {
    return "badge badge-error";
  }
  return "badge badge-warn";
}

export default function DashboardPage() {
  const [todayClasses, setTodayClasses] = useState<ScheduleItem[]>([]);
  const [weeklyClasses, setWeeklyClasses] = useState<ScheduleItem[]>([]);
  const [upcoming, setUpcoming] = useState<Assignment[]>([]);
  const [overdue, setOverdue] = useState<Assignment[]>([]);
  const [portal, setPortal] = useState<PortalStatus | null>(null);
  const [lms, setLms] = useState<LMSStatus | null>(null);
  const [telegram, setTelegram] = useState<TelegramStatus | null>(null);
  const [chatInput, setChatInput] = useState("");
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [chatBusy, setChatBusy] = useState(false);

  useEffect(() => {
    Promise.all([
      api.todaySchedule().catch(() => []),
      api.weeklySchedule().catch(() => []),
      api.upcomingDeadlines(7).catch(() => []),
      api.overdueAssignments().catch(() => []),
      api.portalStatus().catch(() => null),
      api.lmsStatus().catch(() => null),
      api.telegramStatus().catch(() => null),
    ]).then(([t, w, u, o, p, l, tg]) => {
      setTodayClasses(t);
      setWeeklyClasses(w);
      setUpcoming(u);
      setOverdue(o);
      setPortal(p);
      setLms(l);
      setTelegram(tg);
    });
  }, []);

  async function handleChat(e: FormEvent) {
    e.preventDefault();
    const q = chatInput.trim();
    if (!q || chatBusy) return;
    setChatInput("");
    setChatTurns((prev) => [...prev, { role: "user", text: q }]);
    setChatBusy(true);
    try {
      const res = await api.chat(q);
      setChatTurns((prev) => [...prev, { role: "assistant", text: res.reply }]);
    } catch (err: unknown) {
      setChatTurns((prev) => [
        ...prev,
        { role: "assistant", text: err instanceof Error ? err.message : "Không thể gọi AI Agent." },
      ]);
    } finally {
      setChatBusy(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <h2>Tổng quan học tập</h2>
        <span className="muted">Múi giờ hiển thị: Asia/Ho_Chi_Minh</span>
      </div>

      <div className="grid-4">
        <div className="card">
          <h3>Lịch học hôm nay</h3>
          <div className="stat-value">{todayClasses.length}</div>
          <span className="muted">buổi học</span>
        </div>
        <div className="card">
          <h3>Tổng quan tuần</h3>
          <div className="stat-value">{weeklyClasses.length}</div>
          <span className="muted">buổi học trong tuần</span>
        </div>
        <div className="card">
          <h3>Deadline 7 ngày tới</h3>
          <div className="stat-value">{upcoming.length}</div>
          <span className="muted">bài tập sắp đến hạn</span>
        </div>
        <div className="card">
          <h3>Bài tập quá hạn</h3>
          <div className="stat-value" style={{ color: overdue.length > 0 ? "#dc2626" : "var(--brand)" }}>
            {overdue.length}
          </div>
          <span className="muted">cần kiểm tra trên LMS</span>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <h3>Lịch học hôm nay</h3>
          {todayClasses.length === 0 ? (
            <p className="muted">Hôm nay chưa có lịch học nào được đồng bộ.</p>
          ) : (
            todayClasses.map((c) => (
              <div key={c.id} className="uth-card">
                <strong>{c.subject_name || c.subject_code}</strong>
                <div>
                  {c.session} • {c.class_session} • Tiết {c.period_start}–{c.period_end} ({c.start_time.slice(0, 5)}–{c.end_time.slice(0, 5)})
                </div>
                <div>
                  Phòng: {c.room || "Chưa rõ"} {c.campus ? `- ${c.campus}` : ""}
                </div>
              </div>
            ))
          )}
        </div>

        <div className="card">
          <h3>Trạng thái tích hợp</h3>
          <table>
            <tbody>
              <tr>
                <td><strong>Portal UTH</strong></td>
                <td>
                  <span className={statusBadgeClass(portal?.connection_status || "DISCONNECTED")}>
                    {portal?.connection_status || "DISCONNECTED"}
                  </span>
                </td>
                <td className="muted">Sync: {formatLocalDateTime(portal?.last_successful_sync_at)}</td>
              </tr>
              <tr>
                <td><strong>UTH LMS</strong></td>
                <td>
                  <span className={statusBadgeClass(lms?.connection_status || "DISCONNECTED")}>
                    {lms?.connection_status || "DISCONNECTED"}
                  </span>
                </td>
                <td className="muted">Sync: {formatLocalDateTime(lms?.last_successful_sync_at)}</td>
              </tr>
              <tr>
                <td><strong>Telegram Bot</strong></td>
                <td>
                  <span className={telegram?.linked ? "badge badge-ok" : "badge badge-warn"}>
                    {telegram?.linked ? "LINKED" : "UNLINKED"}
                  </span>
                </td>
                <td className="muted">
                  {telegram?.display_name || (telegram?.bot_username ? `@${telegram.bot_username}` : "Chưa liên kết")}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h3>Hỏi nhanh Otter AI Assistant</h3>
        <div className="chat-box">
          {chatTurns.length === 0 ? (
            <span className="muted">
              Ví dụ: &ldquo;Hôm nay mình học môn gì?&rdquo;, &ldquo;Tuần này còn bao nhiêu deadline?&rdquo;
            </span>
          ) : (
            chatTurns.map((turn, idx) => (
              <div
                key={idx}
                className={`chat-msg ${turn.role === "user" ? "chat-user" : "chat-assistant"}`}
              >
                {turn.text}
              </div>
            ))
          )}
        </div>
        <form onSubmit={handleChat} style={{ display: "flex", gap: "0.5rem" }}>
          <input
            className="input"
            placeholder="Nhập câu hỏi cho Otter..."
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
          />
          <button className="btn btn-primary" disabled={chatBusy}>
            {chatBusy ? "Đang gửi..." : "Gửi"}
          </button>
        </form>
      </div>
    </div>
  );
}
