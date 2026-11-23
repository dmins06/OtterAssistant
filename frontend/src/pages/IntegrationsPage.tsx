import { useEffect, useState, type FormEvent } from "react";
import { api, formatLocalDateTime } from "../api/client";
import type { LMSStatus, PortalStatus, TelegramLinkCode, TelegramStatus } from "../types/api";

export default function IntegrationsPage() {
  const [portal, setPortal] = useState<PortalStatus | null>(null);
  const [lms, setLms] = useState<LMSStatus | null>(null);
  const [telegram, setTelegram] = useState<TelegramStatus | null>(null);
  const [tgCode, setTgCode] = useState<TelegramLinkCode | null>(null);

  // Ephemeral inputs — cleared immediately after POST; stored credentials are NEVER displayed
  const [portalJwt, setPortalJwt] = useState("");
  const [lmsMethod, setLmsMethod] = useState("ical_feed");
  const [lmsSecret, setLmsSecret] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    api.portalStatus().then(setPortal).catch(() => null);
    api.lmsStatus().then(setLms).catch(() => null);
    api.telegramStatus().then(setTelegram).catch(() => null);
  }

  useEffect(() => {
    reload();
  }, []);

  async function handleConnectPortal(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    try {
      await api.connectPortal({ access_token: portalJwt });
      setPortalJwt(""); // Clear immediately
      setNotice("Đã cập nhật phiên Portal UTH an toàn (mã hóa at rest).");
      reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Kết nối Portal thất bại.");
    }
  }

  async function handleSyncPortal() {
    setError(null);
    setNotice(null);
    try {
      const res = await api.syncPortal();
      setNotice(`Portal Sync: ${res.status} (+${res.items_added} mới, ${res.items_updated} cập nhật).`);
      reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Đồng bộ Portal thất bại.");
    }
  }

  async function handleConnectLms(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    try {
      await api.connectLms(
        lmsMethod === "moodle_ws"
          ? { integration_method: "moodle_ws", wstoken: lmsSecret }
          : lmsMethod === "ical_feed"
            ? { integration_method: "ical_feed", ical_url: lmsSecret }
            : { integration_method: "mock" }
      );
      setLmsSecret("");
      setNotice("Đã lưu cấu hình kết nối UTH LMS.");
      reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Kết nối LMS thất bại.");
    }
  }

  async function handleSyncLms() {
    setError(null);
    setNotice(null);
    try {
      const res = await api.syncLms();
      setNotice(`LMS Sync: ${res.status} (+${res.items_added} mới, ${res.items_updated} cập nhật).`);
      reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Đồng bộ LMS thất bại.");
    }
  }

  async function handleGenerateTelegramCode() {
    setError(null);
    try {
      const code = await api.createTelegramLinkCode();
      setTgCode(code);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Không thể tạo mã liên kết Telegram.");
    }
  }

  return (
    <div>
      <div className="page-header">
        <h2>Quản lý Tích hợp</h2>
        <span className="muted">Credentials được mã hóa ở backend và không bao giờ hiển thị lại</span>
      </div>

      {notice && <div className="alert alert-ok">{notice}</div>}
      {error && <div className="alert alert-error">{error}</div>}

      <div className="grid-2">
        <div className="card">
          <h3>Portal UTH (portal.ut.edu.vn)</h3>
          <p>
            Trạng thái:{" "}
            <span className={portal?.connection_status === "AUTHENTICATED" ? "badge badge-ok" : "badge badge-warn"}>
              {portal?.connection_status || "DISCONNECTED"}
            </span>
          </p>
          <p className="muted">Lần đồng bộ gần nhất: {formatLocalDateTime(portal?.last_successful_sync_at)}</p>
          {portal?.last_error_code && <p className="muted">Mã lỗi gần nhất: {portal.last_error_code}</p>}

          <form onSubmit={handleConnectPortal}>
            <div className="form-row">
              <label>Cập nhật phiên JWT (Re-authentication)</label>
              <input
                className="input"
                type="password"
                placeholder="Dán Portal JWT token hợp lệ..."
                value={portalJwt}
                onChange={(e) => setPortalJwt(e.target.value)}
                required
              />
            </div>
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button className="btn btn-primary" type="submit">Kết nối / Xác thực lại</button>
              <button className="btn" type="button" onClick={handleSyncPortal}>Manual Sync</button>
              <button
                className="btn btn-danger"
                type="button"
                onClick={async () => {
                  await api.disconnectPortal();
                  reload();
                }}
              >
                Ngắt kết nối
              </button>
            </div>
          </form>
        </div>

        <div className="card">
          <h3>UTH LMS (courses.ut.edu.vn)</h3>
          <p>
            Trạng thái:{" "}
            <span className={lms?.connection_status === "AUTHENTICATED" ? "badge badge-ok" : "badge badge-warn"}>
              {lms?.connection_status || "DISCONNECTED"}
            </span>{" "}
            <span className="muted">({lms?.integration_method || "unconfigured"})</span>
          </p>
          <p className="muted">Lần đồng bộ gần nhất: {formatLocalDateTime(lms?.last_successful_sync_at)}</p>
          {lms?.last_error_code && <p className="muted">Mã lỗi gần nhất: {lms.last_error_code}</p>}

          <form onSubmit={handleConnectLms}>
            <div className="form-row">
              <label>Phương thức tích hợp hợp lệ</label>
              <select
                className="select"
                value={lmsMethod}
                onChange={(e) => setLmsMethod(e.target.value)}
              >
                <option value="ical_feed">Moodle Calendar iCal (.ics URL)</option>
                <option value="moodle_ws">Moodle Web Services (wstoken)</option>
                <option value="mock">Mock LMS (Chế độ kiểm thử)</option>
              </select>
            </div>
            {lmsMethod !== "mock" && (
              <div className="form-row">
                <label>{lmsMethod === "moodle_ws" ? "Moodle wstoken" : "Private iCal (.ics) URL"}</label>
                <input
                  className="input"
                  type="password"
                  value={lmsSecret}
                  onChange={(e) => setLmsSecret(e.target.value)}
                  required
                />
              </div>
            )}
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button className="btn btn-primary" type="submit">Lưu kết nối LMS</button>
              <button className="btn" type="button" onClick={handleSyncLms}>Manual Sync</button>
              <button
                className="btn btn-danger"
                type="button"
                onClick={async () => {
                  await api.disconnectLms();
                  reload();
                }}
              >
                Ngắt kết nối
              </button>
            </div>
          </form>
        </div>

        <div className="card">
          <h3>Telegram Bot</h3>
          <p>
            Trạng thái:{" "}
            <span className={telegram?.linked ? "badge badge-ok" : "badge badge-warn"}>
              {telegram?.linked ? "Đã liên kết" : "Chưa liên kết"}
            </span>
          </p>
          {telegram?.display_name && (
            <p className="muted">Tài khoản Telegram: {telegram.display_name}</p>
          )}
          {telegram?.bot_username && (
            <p className="muted">Bot: @{telegram.bot_username}</p>
          )}
          {tgCode && (
            <div className="alert alert-ok">
              <strong>Mã liên kết 1 lần:</strong> <code>{tgCode.code}</code>
              <div>{tgCode.instruction}</div>
            </div>
          )}
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button className="btn btn-primary" onClick={handleGenerateTelegramCode}>
              Tạo mã liên kết Telegram
            </button>
            {telegram?.linked && (
              <button
                className="btn btn-danger"
                onClick={async () => {
                  await api.unlinkTelegram();
                  reload();
                }}
              >
                Hủy liên kết
              </button>
            )}
          </div>
        </div>

        <div className="card">
          <h3>Google Gemini API (Free Tier)</h3>
          <span className="badge badge-ok">Server-Side Configured</span>
          <p className="muted" style={{ marginTop: "0.6rem" }}>
            API Key và Model ID được cấu hình hoàn toàn phía backend qua biến môi trường (<code>GEMINI_API_KEY</code>, <code>GEMINI_MODEL</code>). Không bao giờ truyền mật khẩu hay JWT tới Gemini.
          </p>
        </div>
      </div>
    </div>
  );
}
