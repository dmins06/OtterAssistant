import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { User, UserPreferences } from "../types/api";

interface Props {
  user: User;
}

export default function SettingsPage({ user }: Props) {
  const [prefs, setPrefs] = useState<UserPreferences | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    api.preferences().then(setPrefs).catch(() => null);
  }, []);

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!prefs) return;
    setSaved(null);
    const updated = await api.updatePreferences(prefs);
    setPrefs(updated);
    setSaved("Đã lưu cấu hình cá nhân.");
  }

  return (
    <div>
      <div className="page-header">
        <h2>Cài đặt &amp; Tài khoản</h2>
      </div>

      {saved && <div className="alert alert-ok">{saved}</div>}

      <div className="grid-2">
        <div className="card">
          <h3>Hồ sơ sinh viên</h3>
          <p><strong>Họ tên:</strong> {user.full_name}</p>
          <p><strong>Email:</strong> {user.email}</p>
          <p><strong>MSSV:</strong> {user.student_code || "Chưa cập nhật"}</p>
        </div>

        <div className="card">
          <h3>Cấu hình nhắc nhở &amp; Tổng hợp</h3>
          {!prefs ? (
            <p className="muted">Đang tải cấu hình...</p>
          ) : (
            <form onSubmit={handleSave}>
              <div className="form-row">
                <label>Múi giờ hiển thị</label>
                <input
                  className="input"
                  value={prefs.timezone}
                  onChange={(e) => setPrefs({ ...prefs, timezone: e.target.value })}
                />
              </div>
              <div className="form-row">
                <label>Nhắc trước giờ học (phút)</label>
                <input
                  className="input"
                  type="number"
                  min={5}
                  max={180}
                  value={prefs.class_reminder_minutes}
                  onChange={(e) =>
                    setPrefs({ ...prefs, class_reminder_minutes: Number(e.target.value) })
                  }
                />
              </div>
              <div className="form-row">
                <label>Nhắc trước deadline (số ngày, cách nhau bởi dấu phẩy)</label>
                <input
                  className="input"
                  value={prefs.assignment_reminder_days}
                  onChange={(e) =>
                    setPrefs({ ...prefs, assignment_reminder_days: e.target.value })
                  }
                />
              </div>
              <div className="form-row">
                <label>Giờ gửi Daily Summary (HH:MM)</label>
                <input
                  className="input"
                  value={prefs.daily_summary_time}
                  onChange={(e) => setPrefs({ ...prefs, daily_summary_time: e.target.value })}
                />
              </div>
              <div className="form-row">
                <label>
                  <input
                    type="checkbox"
                    checked={prefs.daily_summary_enabled}
                    onChange={(e) =>
                      setPrefs({ ...prefs, daily_summary_enabled: e.target.checked })
                    }
                  />{" "}
                  Bật tổng hợp mỗi ngày (Daily Summary)
                </label>
              </div>
              <div className="form-row">
                <label>
                  <input
                    type="checkbox"
                    checked={prefs.weekly_summary_enabled}
                    onChange={(e) =>
                      setPrefs({ ...prefs, weekly_summary_enabled: e.target.checked })
                    }
                  />{" "}
                  Bật tổng hợp mỗi tuần (Weekly Summary)
                </label>
              </div>
              <button className="btn btn-primary" type="submit">Lưu thay đổi</button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
