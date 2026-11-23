import type {
  AgentChatResponse,
  Assignment,
  LMSCourse,
  LMSStatus,
  NotificationLog,
  PortalStatus,
  Reminder,
  ScheduleConflict,
  ScheduleItem,
  Subject,
  TelegramLinkCode,
  TelegramStatus,
  User,
  UserPreferences,
} from "../types/api";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8001/api/v1";

const TOKEN_STORAGE_KEY = "otter_app_token";

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setStoredToken(token: string | null): void {
  if (token) {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const detail = data?.detail || `Request failed (${response.status})`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}

export const api = {
  // Auth
  register: (body: { email: string; full_name: string; password: string; student_code?: string }) =>
    request<User>("/auth/register", { method: "POST", body: JSON.stringify(body) }),
  login: (body: { email: string; password: string }) =>
    request<{ access_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  me: () => request<User>("/auth/me"),

  // Schedule
  todaySchedule: () => request<ScheduleItem[]>("/schedules/today"),
  weeklySchedule: (refDate?: string) =>
    request<ScheduleItem[]>(refDate ? `/schedules/weekly?ref_date=${encodeURIComponent(refDate)}` : "/schedules/weekly"),
  scheduleByDate: (targetDate: string) =>
    request<ScheduleItem[]>(`/schedules/by-date?target_date=${encodeURIComponent(targetDate)}`),
  scheduleConflicts: () => request<ScheduleConflict[]>("/schedules/conflicts"),

  // Subjects & LMS Courses
  subjects: (q?: string) =>
    request<Subject[]>(q ? `/subjects?q=${encodeURIComponent(q)}` : "/subjects"),
  lmsCourses: () => request<LMSCourse[]>("/integrations/lms/courses"),

  // Assignments
  assignments: (status?: string) =>
    request<Assignment[]>(
      status && status !== "ALL"
        ? `/assignments?submission_status=${encodeURIComponent(status)}`
        : "/assignments"
    ),
  upcomingDeadlines: (days = 7) => request<Assignment[]>(`/assignments/upcoming?days=${days}`),
  overdueAssignments: () => request<Assignment[]>("/assignments/overdue"),

  // Reminders & Notifications
  reminders: () => request<Reminder[]>("/reminders"),
  createReminder: (body: { title: string; notes?: string; remind_at: string; target_type?: string }) =>
    request<Reminder>("/reminders", { method: "POST", body: JSON.stringify(body) }),
  updateReminder: (id: number, body: Partial<{ title: string; notes: string; remind_at: string; is_enabled: boolean }>) =>
    request<Reminder>(`/reminders/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteReminder: (id: number) => request<void>(`/reminders/${id}`, { method: "DELETE" }),
  notificationLogs: () => request<NotificationLog[]>("/notifications/logs"),

  // Integrations (safe status — never returns credentials)
  portalStatus: () => request<PortalStatus>("/integrations/portal/status"),
  connectPortal: (body: { access_token: string; refresh_token?: string }) =>
    request<PortalStatus>("/integrations/portal/session", { method: "POST", body: JSON.stringify(body) }),
  disconnectPortal: () => request<PortalStatus>("/integrations/portal/session", { method: "DELETE" }),
  syncPortal: () => request<{ status: string; items_added: number; items_updated: number; error_message?: string }>("/integrations/portal/sync", { method: "POST", body: JSON.stringify({}) }),

  lmsStatus: () => request<LMSStatus>("/integrations/lms/status"),
  connectLms: (body: { integration_method: string; wstoken?: string; ical_url?: string }) =>
    request<LMSStatus>("/integrations/lms/connect", { method: "POST", body: JSON.stringify(body) }),
  disconnectLms: () => request<LMSStatus>("/integrations/lms/connect", { method: "DELETE" }),
  syncLms: () => request<{ status: string; items_added: number; items_updated: number; error_message?: string }>("/integrations/lms/sync", { method: "POST" }),

  telegramStatus: () => request<TelegramStatus>("/integrations/telegram/status"),
  createTelegramLinkCode: () => request<TelegramLinkCode>("/integrations/telegram/link-code", { method: "POST" }),
  unlinkTelegram: () => request<TelegramStatus>("/integrations/telegram/link", { method: "DELETE" }),

  // Preferences
  preferences: () => request<UserPreferences>("/preferences"),
  updatePreferences: (body: Partial<UserPreferences>) =>
    request<UserPreferences>("/preferences", { method: "PATCH", body: JSON.stringify(body) }),

  // AI Agent
  chat: (message: string) =>
    request<AgentChatResponse>("/agent/chat", {
      method: "POST",
      body: JSON.stringify({ message, channel: "dashboard" }),
    }),
};

export function formatLocalDateTime(iso: string | null | undefined): string {
  if (!iso) return "Chưa có";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}
