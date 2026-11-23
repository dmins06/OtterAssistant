import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ScheduleConflict, ScheduleItem } from "../types/api";

const WEEKDAYS = [
  { weekday: 2, label: "Thứ 2" },
  { weekday: 3, label: "Thứ 3" },
  { weekday: 4, label: "Thứ 4" },
  { weekday: 5, label: "Thứ 5" },
  { weekday: 6, label: "Thứ 6" },
  { weekday: 7, label: "Thứ 7" },
  { weekday: 8, label: "Chủ nhật" },
];

const UTH_SLOTS: Array<{ session: "Sáng" | "Chiều" | "Tối"; ca: string }> = [
  { session: "Sáng", ca: "Ca 1" },
  { session: "Sáng", ca: "Ca 2" },
  { session: "Chiều", ca: "Ca 3" },
  { session: "Chiều", ca: "Ca 4" },
  { session: "Tối", ca: "Ca 5" },
];

export default function SchedulePage() {
  const [viewMode, setViewMode] = useState<"weekly" | "daily">("weekly");
  const [refDate, setRefDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [items, setItems] = useState<ScheduleItem[]>([]);
  const [conflicts, setConflicts] = useState<ScheduleConflict[]>([]);

  useEffect(() => {
    const loader =
      viewMode === "weekly" ? api.weeklySchedule(refDate) : api.scheduleByDate(refDate);
    loader.then(setItems).catch(() => setItems([]));
    api.scheduleConflicts().then(setConflicts).catch(() => setConflicts([]));
  }, [viewMode, refDate]);

  return (
    <div>
      <div className="page-header">
        <h2>Thời khóa biểu UTH</h2>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <input
            type="date"
            className="input"
            style={{ width: "auto" }}
            value={refDate}
            onChange={(e) => setRefDate(e.target.value)}
          />
          <button
            className={`btn ${viewMode === "weekly" ? "btn-primary" : ""}`}
            onClick={() => setViewMode("weekly")}
          >
            Tuần (UTH Grid)
          </button>
          <button
            className={`btn ${viewMode === "daily" ? "btn-primary" : ""}`}
            onClick={() => setViewMode("daily")}
          >
            Theo ngày
          </button>
        </div>
      </div>

      {conflicts.length > 0 && (
        <div className="alert alert-error">
          <strong>Phát hiện {conflicts.length} lịch trùng:</strong>{" "}
          {conflicts
            .map(
              (c) =>
                `${c.class_date}: ${c.first_subject_name} trùng ${c.second_subject_name} (${c.overlap_start.slice(0, 5)}–${c.overlap_end.slice(0, 5)})`
            )
            .join("; ")}
        </div>
      )}

      {viewMode === "weekly" ? (
        <div className="card table-wrap">
          <table className="uth-grid">
            <thead>
              <tr>
                <th>Buổi</th>
                <th>Ca học</th>
                {WEEKDAYS.map((w) => (
                  <th key={w.weekday}>{w.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {UTH_SLOTS.map((slot) => (
                <tr key={`${slot.session}-${slot.ca}`}>
                  <td style={{ fontWeight: 600 }}>{slot.session}</td>
                  <td>{slot.ca}</td>
                  {WEEKDAYS.map((w) => {
                    const cellItems = items.filter(
                      (i) =>
                        i.weekday === w.weekday &&
                        i.session === slot.session &&
                        i.class_session.toLowerCase() === slot.ca.toLowerCase()
                    );
                    return (
                      <td key={w.weekday} style={{ minWidth: "155px" }}>
                        {cellItems.map((c) => (
                          <div key={c.id} className="uth-card">
                            <strong>{c.subject_name || c.subject_code}</strong>
                            <div className="muted">{c.subject_code}</div>
                            <div>
                              Tiết: {c.period_start} - {c.period_end}
                            </div>
                            <div>
                              {c.start_time.slice(0, 5)} - {c.end_time.slice(0, 5)}
                            </div>
                            <div>
                              Phòng: {c.room || "?"} {c.campus ? `- ${c.campus}` : ""}
                            </div>
                            {c.location && <div className="muted">({c.location})</div>}
                            <div className="uth-links">
                              {c.syllabus_url && (
                                <a href={c.syllabus_url} target="_blank" rel="noreferrer">
                                  Đề cương
                                </a>
                              )}
                              {c.lms_url && (
                                <a href={c.lms_url} target="_blank" rel="noreferrer">
                                  LMS
                                </a>
                              )}
                            </div>
                          </div>
                        ))}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card">
          <h3>Lịch học ngày {refDate}</h3>
          {items.length === 0 ? (
            <p className="muted">Ngày {refDate} không có lịch học.</p>
          ) : (
            items.map((c) => (
              <div key={c.id} className="uth-card">
                <strong>
                  {c.subject_name} ({c.subject_code})
                </strong>
                <div>
                  {c.session} • {c.class_session} • Tiết {c.period_start}–{c.period_end} ({c.start_time.slice(0, 5)}–{c.end_time.slice(0, 5)})
                </div>
                <div>
                  Phòng: {c.room || "?"} {c.campus ? `- ${c.campus}` : ""}{" "}
                  {c.location ? `(${c.location})` : ""}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
