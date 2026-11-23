import { useEffect, useState } from "react";
import { api, formatLocalDateTime } from "../api/client";
import type { Assignment, SubmissionStatus } from "../types/api";

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "ALL", label: "Tất cả trạng thái" },
  { value: "NOT_SUBMITTED", label: "Chưa nộp" },
  { value: "OVERDUE", label: "Quá hạn" },
  { value: "SUBMITTED", label: "Đã nộp" },
  { value: "GRADED", label: "Đã chấm điểm" },
  { value: "NO_DEADLINE", label: "Không có deadline" },
  { value: "UNKNOWN", label: "Chưa xác định" },
];

function badgeForStatus(status: SubmissionStatus): string {
  switch (status) {
    case "SUBMITTED":
    case "GRADED":
      return "badge badge-ok";
    case "OVERDUE":
      return "badge badge-error";
    case "NOT_SUBMITTED":
      return "badge badge-warn";
    default:
      return "badge badge-neutral";
  }
}

export default function AssignmentsPage() {
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<Assignment[]>([]);
  const [selected, setSelected] = useState<Assignment | null>(null);

  useEffect(() => {
    api.assignments(statusFilter).then(setItems).catch(() => setItems([]));
  }, [statusFilter]);

  const filtered = items.filter((a) =>
    a.title.toLowerCase().includes(search.trim().toLowerCase())
  );

  return (
    <div>
      <div className="page-header">
        <h2>Bài tập &amp; Deadline (UTH LMS)</h2>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <input
            className="input"
            style={{ width: "220px" }}
            placeholder="Tìm bài tập..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select
            className="select"
            style={{ width: "200px" }}
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid-2">
        <div className="card table-wrap">
          {filtered.length === 0 ? (
            <p className="muted">Không tìm thấy bài tập phù hợp.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Bài tập</th>
                  <th>Deadline</th>
                  <th>Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((a) => (
                  <tr
                    key={a.id}
                    onClick={() => setSelected(a)}
                    style={{ cursor: "pointer", background: selected?.id === a.id ? "#f0fdfa" : undefined }}
                  >
                    <td><strong>{a.title}</strong></td>
                    <td>{formatLocalDateTime(a.due_at)}</td>
                    <td>
                      <span className={badgeForStatus(a.submission_status)}>
                        {a.submission_status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <h3>Chi tiết bài tập</h3>
          {!selected ? (
            <p className="muted">Chọn một bài tập bên trái để xem chi tiết.</p>
          ) : (
            <div>
              <h4 style={{ marginTop: 0 }}>{selected.title}</h4>
              <p>
                <strong>Trạng thái:</strong>{" "}
                <span className={badgeForStatus(selected.submission_status)}>
                  {selected.submission_status}
                </span>
              </p>
              <p><strong>Hạn nộp:</strong> {formatLocalDateTime(selected.due_at)}</p>
              <p><strong>Thời gian nộp:</strong> {formatLocalDateTime(selected.submitted_at)}</p>
              <p><strong>Điểm số:</strong> {selected.grade ?? "Chưa có"}</p>
              {selected.feedback && <p><strong>Nhận xét:</strong> {selected.feedback}</p>}
              {selected.description && (
                <p style={{ whiteSpace: "pre-wrap" }}>
                  <strong>Mô tả:</strong> {selected.description}
                </p>
              )}
              {selected.assignment_url && (
                <a className="btn btn-primary" href={selected.assignment_url} target="_blank" rel="noreferrer">
                  Mở trên UTH LMS
                </a>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
