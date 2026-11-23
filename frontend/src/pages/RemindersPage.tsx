import { useEffect, useState, type FormEvent } from "react";
import { api, formatLocalDateTime } from "../api/client";
import type { NotificationLog, Reminder } from "../types/api";

export default function RemindersPage() {
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [logs, setLogs] = useState<NotificationLog[]>([]);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [remindAt, setRemindAt] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);

  function loadAll() {
    api.reminders().then(setReminders).catch(() => setReminders([]));
    api.notificationLogs().then(setLogs).catch(() => setLogs([]));
  }

  useEffect(() => {
    loadAll();
  }, []);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!title.trim() || !remindAt) return;
    setFeedback(null);
    try {
      await api.createReminder({
        title: title.trim(),
        notes: notes.trim() || undefined,
        remind_at: new Date(remindAt).toISOString(),
      });
      setTitle("");
      setNotes("");
      setRemindAt("");
      setFeedback("Đã tạo nhắc nhở mới.");
      loadAll();
    } catch (err: unknown) {
      setFeedback(err instanceof Error ? err.message : "Không thể tạo nhắc nhở.");
    }
  }

  async function toggleReminder(r: Reminder) {
    await api.updateReminder(r.id, { is_enabled: !r.is_enabled });
    loadAll();
  }

  async function removeReminder(id: number) {
    await api.deleteReminder(id);
    loadAll();
  }

  return (
    <div>
      <div className="page-header">
        <h2>Nhắc nhở &amp; Lịch sử thông báo</h2>
      </div>

      {feedback && <div className="alert alert-ok">{feedback}</div>}

      <div className="grid-2">
        <div className="card">
          <h3>Tạo nhắc nhở mới</h3>
          <form onSubmit={handleCreate}>
            <div className="form-row">
              <label>Tiêu đề</label>
              <input
                className="input"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="VD: Chuẩn bị slide báo cáo Học sâu"
                required
              />
            </div>
            <div className="form-row">
              <label>Thời gian nhắc</label>
              <input
                className="input"
                type="datetime-local"
                value={remindAt}
                onChange={(e) => setRemindAt(e.target.value)}
                required
              />
            </div>
            <div className="form-row">
              <label>Ghi chú (tuỳ chọn)</label>
              <input
                className="input"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </div>
            <button className="btn btn-primary">Tạo nhắc nhở</button>
          </form>
        </div>

        <div className="card table-wrap">
          <h3>Danh sách nhắc nhở</h3>
          {reminders.length === 0 ? (
            <p className="muted">Chưa có nhắc nhở nào.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Tiêu đề</th>
                  <th>Thời gian</th>
                  <th>Trạng thái</th>
                  <th>Hành động</th>
                </tr>
              </thead>
              <tbody>
                {reminders.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <strong>{r.title}</strong>
                      {r.notes && <div className="muted">{r.notes}</div>}
                    </td>
                    <td>{formatLocalDateTime(r.remind_at)}</td>
                    <td>
                      <span className={r.is_enabled ? "badge badge-ok" : "badge badge-neutral"}>
                        {r.is_sent ? "Đã gửi" : r.is_enabled ? "Đang bật" : "Đã tắt"}
                      </span>
                    </td>
                    <td style={{ display: "flex", gap: "0.35rem" }}>
                      <button className="btn" onClick={() => toggleReminder(r)}>
                        {r.is_enabled ? "Tắt" : "Bật"}
                      </button>
                      <button className="btn btn-danger" onClick={() => removeReminder(r.id)}>
                        Xóa
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="card table-wrap">
        <h3>Lịch sử gửi thông báo</h3>
        {logs.length === 0 ? (
          <p className="muted">Chưa có lịch sử gửi thông báo.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Loại</th>
                <th>Kênh</th>
                <th>Trạng thái</th>
                <th>Thời gian</th>
                <th>Nội dung / Chi tiết</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id}>
                  <td>{log.notification_type}</td>
                  <td>{log.channel}</td>
                  <td>
                    <span
                      className={
                        log.status === "SENT"
                          ? "badge badge-ok"
                          : log.status === "POLICY_RESTRICTED"
                            ? "badge badge-warn"
                            : "badge badge-error"
                      }
                    >
                      {log.status}
                    </span>
                  </td>
                  <td>{formatLocalDateTime(log.sent_at || log.scheduled_for)}</td>
                  <td className="muted">{log.error_detail || log.payload_summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
