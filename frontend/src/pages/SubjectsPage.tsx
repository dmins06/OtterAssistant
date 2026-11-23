import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { LMSCourse, Subject } from "../types/api";

export default function SubjectsPage() {
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [lmsCourses, setLmsCourses] = useState<LMSCourse[]>([]);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api.subjects(query || undefined).then(setSubjects).catch(() => setSubjects([]));
    api.lmsCourses().then(setLmsCourses).catch(() => setLmsCourses([]));
  }, [query]);

  return (
    <div>
      <div className="page-header">
        <h2>Học phần &amp; Liên kết LMS</h2>
        <input
          className="input"
          style={{ maxWidth: "280px" }}
          placeholder="Tìm theo tên hoặc mã học phần..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="card table-wrap" style={{ marginBottom: "1.25rem" }}>
        <h3>Danh sách học phần (Portal UTH)</h3>
        {subjects.length === 0 ? (
          <p className="muted">Chưa có học phần nào. Hãy đồng bộ từ Portal UTH trong mục Integrations.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Mã học phần</th>
                <th>Tên học phần</th>
                <th>Tín chỉ</th>
                <th>Liên kết</th>
              </tr>
            </thead>
            <tbody>
              {subjects.map((s) => (
                <tr key={s.id}>
                  <td><code>{s.subject_code}</code></td>
                  <td><strong>{s.name}</strong></td>
                  <td>{s.credits ?? "—"}</td>
                  <td>
                    <div className="uth-links">
                      {s.syllabus_url && (
                        <a href={s.syllabus_url} target="_blank" rel="noreferrer">
                          Đề cương
                        </a>
                      )}
                      {s.lms_url && (
                        <a href={s.lms_url} target="_blank" rel="noreferrer">
                          Mở LMS
                        </a>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card table-wrap">
        <h3>Course Mapping (UTH LMS ↔ Portal)</h3>
        {lmsCourses.length === 0 ? (
          <p className="muted">Chưa đồng bộ khóa học nào từ UTH LMS.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>LMS Course ID</th>
                <th>Tên khóa học</th>
                <th>Mã học phần</th>
                <th>Trạng thái Mapping</th>
              </tr>
            </thead>
            <tbody>
              {lmsCourses.map((c) => (
                <tr key={c.id}>
                  <td><code>{c.external_course_id}</code></td>
                  <td>{c.course_name}</td>
                  <td>{c.course_code || "—"}</td>
                  <td>
                    <span
                      className={
                        c.mapping_status === "MAPPED"
                          ? "badge badge-ok"
                          : c.mapping_status === "AMBIGUOUS"
                            ? "badge badge-error"
                            : "badge badge-warn"
                      }
                    >
                      {c.mapping_status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
