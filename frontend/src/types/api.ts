export interface User {
  id: number;
  email: string;
  student_code: string | null;
  full_name: string;
  is_active: boolean;
  created_at: string;
}

export interface Subject {
  id: number;
  subject_code: string;
  name: string;
  credits: number | null;
  lecturer: string | null;
  syllabus_url: string | null;
  lms_url: string | null;
  last_synced_at: string | null;
}

export interface ScheduleItem {
  id: number;
  subject_id: number;
  subject_code: string | null;
  subject_name: string | null;
  source_event_id: string;
  class_date: string;
  weekday: number;
  session: "Sáng" | "Chiều" | "Tối";
  class_session: string;
  period_start: number;
  period_end: number;
  start_time: string;
  end_time: string;
  room: string | null;
  campus: string | null;
  location: string | null;
  raw_room_text: string | null;
  syllabus_url: string | null;
  lms_url: string | null;
  last_synced_at: string | null;
}

export interface ScheduleConflict {
  class_date: string;
  first_schedule_id: number;
  second_schedule_id: number;
  first_subject_name: string | null;
  second_subject_name: string | null;
  overlap_start: string;
  overlap_end: string;
}

export type SubmissionStatus =
  | "NOT_SUBMITTED"
  | "SUBMITTED"
  | "GRADED"
  | "OVERDUE"
  | "NO_DEADLINE"
  | "UNKNOWN";

export interface Assignment {
  id: number;
  subject_id: number | null;
  lms_course_id: number;
  external_assignment_id: string;
  title: string;
  description: string | null;
  due_at: string | null;
  submission_status: SubmissionStatus;
  submitted_at: string | null;
  grade: number | null;
  feedback: string | null;
  assignment_url: string | null;
  last_synced_at: string | null;
}

export interface LMSCourse {
  id: number;
  subject_id: number | null;
  external_course_id: string;
  course_code: string | null;
  course_name: string;
  course_url: string | null;
  mapping_status: "MAPPED" | "UNMAPPED" | "AMBIGUOUS";
  last_synced_at: string | null;
}

export interface Reminder {
  id: number;
  target_type: "SCHEDULE" | "ASSIGNMENT" | "CUSTOM";
  target_id: number | null;
  title: string;
  notes: string | null;
  remind_at: string;
  is_enabled: boolean;
  is_sent: boolean;
}

export interface NotificationLog {
  id: number;
  notification_type: string;
  channel: string;
  status: string;
  scheduled_for: string;
  sent_at: string | null;
  payload_summary: string | null;
  error_detail: string | null;
}

export interface PortalStatus {
  provider: string;
  connection_status: string;
  is_mock: boolean;
  supports_refresh: boolean;
  access_token_expires_at: string | null;
  last_authenticated_at: string | null;
  last_refreshed_at: string | null;
  last_successful_sync_at: string | null;
  last_error_code: string | null;
}

export interface LMSStatus {
  provider: string;
  integration_method: string;
  connection_status: string;
  is_mock: boolean;
  last_authenticated_at: string | null;
  last_successful_sync_at: string | null;
  last_error_code: string | null;
}

export interface TelegramStatus {
  linked: boolean;
  display_name: string | null;
  bot_username: string | null;
  last_interaction_at: string | null;
}

export interface TelegramLinkCode {
  code: string;
  expires_at: string;
  bot_username: string | null;
  instruction: string;
}

export interface UserPreferences {
  timezone: string;
  class_reminder_minutes: number;
  assignment_reminder_days: string;
  daily_summary_enabled: boolean;
  daily_summary_time: string;
  weekly_summary_enabled: boolean;
  weekly_summary_day: number;
}

export interface AgentChatResponse {
  reply: string;
  tools_used: string[];
  prompt_tokens: number;
  completion_tokens: number;
  degraded: boolean;
}
