from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import get_app_tz, local_today
from app.models.academic import Assignment, Schedule
from app.models.connection import LMSConnection, PortalConnection
from app.models.enums import ConnectionStatus, ReminderTargetType, SubmissionStatus
from app.schemas.reminder import ReminderCreate, ReminderUpdate
from app.services.assignment_service import AssignmentService
from app.services.reminder_service import ReminderService
from app.services.schedule_service import ScheduleService
from app.services.subject_service import SubjectService
from app.tools.registry import ToolDefinition, ToolRegistry


class EmptyInput(BaseModel):
    pass


class DateInput(BaseModel):
    target_date: str = Field(..., description="Calendar date in YYYY-MM-DD format")


class OptionalDateInput(BaseModel):
    ref_date: str | None = Field(default=None, description="Optional reference date YYYY-MM-DD")


class SubjectQueryInput(BaseModel):
    query: str = Field(..., min_length=1, description="Subject name or subject code")


class AssignmentFilterInput(BaseModel):
    status: str | None = Field(default=None, description="Optional SubmissionStatus filter")


class AssignmentBySubjectInput(BaseModel):
    subject_query: str = Field(..., min_length=1, description="Subject name or subject code")


class UpcomingDeadlinesInput(BaseModel):
    days: int = Field(default=7, ge=1, le=90, description="Number of days ahead to check")


class AssignmentIdInput(BaseModel):
    assignment_id: int = Field(..., ge=1, description="Assignment ID")


class ReminderCreateInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    remind_at_iso: str = Field(..., description="ISO-8601 reminder timestamp (e.g. 2026-12-08T07:30:00+07:00)")
    notes: str | None = None
    target_type: str = Field(default="CUSTOM", description="SCHEDULE | ASSIGNMENT | CUSTOM")
    target_id: int | None = None


class ReminderUpdateInput(BaseModel):
    reminder_id: int = Field(..., ge=1)
    title: str | None = None
    notes: str | None = None
    remind_at_iso: str | None = None
    is_enabled: bool | None = None


class ReminderIdInput(BaseModel):
    reminder_id: int = Field(..., ge=1)


def _format_dt_local(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    tz = get_app_tz()
    if dt.tzinfo is None:
        return dt.isoformat()
    return dt.astimezone(tz).isoformat()


def _serialize_schedule(s: Schedule) -> dict[str, Any]:
    return {
        "id": s.id,
        "subject_code": s.subject.subject_code if s.subject else None,
        "subject_name": s.subject.name if s.subject else None,
        "class_date": s.class_date.isoformat(),
        "weekday": s.weekday,
        "session": s.session.value,
        "class_session": s.class_session,
        "period": f"Tiết {s.period_start}-{s.period_end}",
        "start_time": s.start_time.strftime("%H:%M"),
        "end_time": s.end_time.strftime("%H:%M"),
        "room": s.room,
        "campus": s.campus,
        "location": s.location,
        "lms_url": s.lms_url,
        "last_synced_at": _format_dt_local(s.last_synced_at),
    }


def _serialize_assignment(a: Assignment) -> dict[str, Any]:
    return {
        "id": a.id,
        "subject_id": a.subject_id,
        "title": a.title,
        "due_at_local": _format_dt_local(a.due_at),
        "submission_status": a.submission_status.value,
        "submitted_at_local": _format_dt_local(a.submitted_at),
        "grade": a.grade,
        "feedback": a.feedback,
        "assignment_url": a.assignment_url,
        "last_synced_at": _format_dt_local(a.last_synced_at),
    }


def build_academic_tool_registry(db: Session) -> ToolRegistry:
    """Register all Section 8 tools bound to the current DB session (strictly user-isolated)."""
    registry = ToolRegistry()
    sched_svc = ScheduleService(db)
    subj_svc = SubjectService(db)
    assign_svc = AssignmentService(db)
    rem_svc = ReminderService(db)

    def _connection_meta(user_id: int) -> dict[str, Any]:
        p_conn = db.scalar(select(PortalConnection).where(PortalConnection.user_id == user_id))
        l_conn = db.scalar(select(LMSConnection).where(LMSConnection.user_id == user_id))
        return {
            "portal": {
                "status": p_conn.connection_status.value if p_conn else ConnectionStatus.DISCONNECTED.value,
                "last_successful_sync_at": _format_dt_local(p_conn.last_successful_sync_at) if p_conn else None,
            },
            "lms": {
                "status": l_conn.connection_status.value if l_conn else ConnectionStatus.DISCONNECTED.value,
                "last_successful_sync_at": _format_dt_local(l_conn.last_successful_sync_at) if l_conn else None,
            },
        }

    # --- Schedule tools ---
    registry.register(
        ToolDefinition(
            name="get_today_schedule",
            description="Lấy thời khóa biểu hôm nay của sinh viên.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "date": local_today().isoformat(),
                "classes": [_serialize_schedule(s) for s in sched_svc.get_today_schedule(uid)],
                "sync": _connection_meta(uid)["portal"],
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_tomorrow_schedule",
            description="Lấy thời khóa biểu ngày mai của sinh viên.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "classes": [_serialize_schedule(s) for s in sched_svc.get_tomorrow_schedule(uid)],
                "sync": _connection_meta(uid)["portal"],
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_weekly_schedule",
            description="Lấy thời khóa biểu cả tuần (Thứ 2 đến Chủ nhật).",
            input_model=OptionalDateInput,
            handler=lambda uid, args: {
                "classes": [
                    _serialize_schedule(s)
                    for s in sched_svc.get_weekly_schedule(
                        uid,
                        reference_date=date.fromisoformat(args.ref_date) if args.ref_date else None,
                    )
                ],
                "sync": _connection_meta(uid)["portal"],
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_schedule_by_date",
            description="Tra cứu thời khóa biểu theo một ngày cụ thể (YYYY-MM-DD).",
            input_model=DateInput,
            handler=lambda uid, args: {
                "date": args.target_date,
                "classes": [
                    _serialize_schedule(s)
                    for s in sched_svc.get_schedule_by_date(uid, date.fromisoformat(args.target_date))
                ],
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_subject_details",
            description="Tìm thông tin học phần theo tên hoặc mã học phần.",
            input_model=SubjectQueryInput,
            handler=lambda uid, args: {
                "subjects": [
                    {
                        "id": s.id,
                        "subject_code": s.subject_code,
                        "name": s.name,
                        "credits": s.credits,
                        "lecturer": s.lecturer,
                        "syllabus_url": s.syllabus_url,
                        "lms_url": s.lms_url,
                    }
                    for s in subj_svc.search_subjects(uid, args.query)
                ]
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_next_class",
            description="Lấy buổi học sắp diễn ra tiếp theo.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "next_class": (
                    _serialize_schedule(nxt) if (nxt := sched_svc.get_next_class(uid)) else None
                )
            },
        )
    )

    # --- Assignment tools ---
    def _handle_get_assignments(uid: int, args: AssignmentFilterInput) -> dict[str, Any]:
        status_enum = SubmissionStatus(args.status) if args.status else None
        return {
            "assignments": [
                _serialize_assignment(a)
                for a in assign_svc.list_assignments(uid, status=status_enum)
            ],
            "sync": _connection_meta(uid)["lms"],
        }

    registry.register(
        ToolDefinition(
            name="get_assignments",
            description="Lấy danh sách bài tập trên LMS, có thể lọc theo trạng thái nộp bài.",
            input_model=AssignmentFilterInput,
            handler=_handle_get_assignments,
        )
    )

    def _handle_assignments_by_subject(uid: int, args: AssignmentBySubjectInput) -> dict[str, Any]:
        matched = subj_svc.search_subjects(uid, args.subject_query)
        results: list[dict[str, Any]] = []
        for subj in matched:
            for a in assign_svc.list_assignments(uid, subject_id=subj.id):
                item = _serialize_assignment(a)
                item["subject_name"] = subj.name
                results.append(item)
        return {"matched_subjects": [s.name for s in matched], "assignments": results}

    registry.register(
        ToolDefinition(
            name="get_assignments_by_subject",
            description="Lấy danh sách bài tập theo tên hoặc mã học phần.",
            input_model=AssignmentBySubjectInput,
            handler=_handle_assignments_by_subject,
        )
    )
    registry.register(
        ToolDefinition(
            name="get_upcoming_deadlines",
            description="Lấy các bài tập sắp đến hạn trong N ngày tới (mặc định 7 ngày).",
            input_model=UpcomingDeadlinesInput,
            handler=lambda uid, args: {
                "days": args.days,
                "assignments": [
                    _serialize_assignment(a)
                    for a in assign_svc.get_upcoming_deadlines(uid, days=args.days)
                ],
                "sync": _connection_meta(uid)["lms"],
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_today_deadlines",
            description="Lấy các bài tập có deadline trong ngày hôm nay.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "assignments": [
                    _serialize_assignment(a) for a in assign_svc.get_today_deadlines(uid)
                ],
                "sync": _connection_meta(uid)["lms"],
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_overdue_assignments",
            description="Lấy danh sách bài tập đã quá hạn.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "assignments": [
                    _serialize_assignment(a) for a in assign_svc.get_overdue_assignments(uid)
                ],
                "note": "Quá hạn không mặc định là LMS đã khóa nộp bài.",
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_assignment_details",
            description="Xem chi tiết một bài tập cụ thể theo ID.",
            input_model=AssignmentIdInput,
            handler=lambda uid, args: {
                "assignment": (
                    _serialize_assignment(a)
                    if (a := assign_svc.get_assignment_detail(uid, args.assignment_id))
                    else None
                )
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_submission_status",
            description="Kiểm tra trạng thái nộp bài và điểm số của một bài tập theo ID.",
            input_model=AssignmentIdInput,
            handler=lambda uid, args: (
                {
                    "assignment_id": a.id,
                    "title": a.title,
                    "submission_status": a.submission_status.value,
                    "submitted_at_local": _format_dt_local(a.submitted_at),
                    "grade": a.grade,
                    "feedback": a.feedback,
                }
                if (a := assign_svc.get_assignment_detail(uid, args.assignment_id))
                else {"assignment_id": args.assignment_id, "found": False}
            ),
        )
    )

    # --- Reminder tools ---
    registry.register(
        ToolDefinition(
            name="create_reminder",
            description="Tạo một nhắc nhở mới cho sinh viên.",
            input_model=ReminderCreateInput,
            handler=lambda uid, args: (
                lambda r: {
                    "id": r.id,
                    "title": r.title,
                    "remind_at_local": _format_dt_local(r.remind_at),
                    "is_enabled": r.is_enabled,
                }
            )(
                rem_svc.create_reminder(
                    uid,
                    ReminderCreate(
                        target_type=ReminderTargetType(args.target_type),
                        target_id=args.target_id,
                        title=args.title,
                        notes=args.notes,
                        remind_at=datetime.fromisoformat(args.remind_at_iso),
                    ),
                )
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="update_reminder",
            description="Cập nhật hoặc bật/tắt một nhắc nhở theo ID.",
            input_model=ReminderUpdateInput,
            handler=lambda uid, args: (
                {"updated": True, "id": r.id, "title": r.title, "is_enabled": r.is_enabled}
                if (
                    r := rem_svc.update_reminder(
                        uid,
                        args.reminder_id,
                        ReminderUpdate(
                            title=args.title,
                            notes=args.notes,
                            remind_at=datetime.fromisoformat(args.remind_at_iso) if args.remind_at_iso else None,
                            is_enabled=args.is_enabled,
                        ),
                    )
                )
                else {"updated": False, "error": "Reminder not found"}
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="delete_reminder",
            description="Xóa một nhắc nhở theo ID.",
            input_model=ReminderIdInput,
            handler=lambda uid, args: {"deleted": rem_svc.delete_reminder(uid, args.reminder_id)},
        )
    )
    registry.register(
        ToolDefinition(
            name="get_reminders",
            description="Lấy danh sách nhắc nhở hiện có của sinh viên.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "reminders": [
                    {
                        "id": r.id,
                        "title": r.title,
                        "remind_at_local": _format_dt_local(r.remind_at),
                        "is_enabled": r.is_enabled,
                        "is_sent": r.is_sent,
                    }
                    for r in rem_svc.list_reminders(uid)
                ]
            },
        )
    )

    # --- Account / Connection tools ---
    registry.register(
        ToolDefinition(
            name="get_connection_status",
            description="Kiểm tra trạng thái kết nối Portal UTH và UTH LMS (không bao gồm credentials).",
            input_model=EmptyInput,
            handler=lambda uid, _a: _connection_meta(uid),
        )
    )
    registry.register(
        ToolDefinition(
            name="request_portal_reauthentication",
            description="Hướng dẫn người dùng xác thực lại Portal UTH an toàn trên Dashboard.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "action_required": True,
                "message": "Vui lòng mở trang Integrations trên Otter Dashboard để cập nhật phiên đăng nhập Portal UTH.",
                "portal_status": _connection_meta(uid)["portal"]["status"],
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="sync_portal_data",
            description="Kiểm tra khả năng đồng bộ dữ liệu Portal UTH.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "portal": _connection_meta(uid)["portal"],
                "hint": "Dùng nút Manual Sync trên Dashboard hoặc endpoint /api/v1/integrations/portal/sync để đồng bộ ngay.",
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="sync_lms_data",
            description="Kiểm tra khả năng đồng bộ dữ liệu bài tập UTH LMS.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "lms": _connection_meta(uid)["lms"],
                "hint": "Dùng nút Manual Sync trên Dashboard hoặc endpoint /api/v1/integrations/lms/sync để đồng bộ ngay.",
            },
        )
    )

    # --- Dashboard summary tools ---
    registry.register(
        ToolDefinition(
            name="get_daily_summary",
            description="Tổng hợp lịch học hôm nay, deadline hôm nay và bài tập quá hạn.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "date": local_today().isoformat(),
                "today_classes": [_serialize_schedule(s) for s in sched_svc.get_today_schedule(uid)],
                "today_deadlines": [_serialize_assignment(a) for a in assign_svc.get_today_deadlines(uid)],
                "overdue_count": len(assign_svc.get_overdue_assignments(uid)),
                "connections": _connection_meta(uid),
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_weekly_summary",
            description="Tổng hợp lịch học tuần này và deadline trong 7 ngày tới.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "weekly_classes": [_serialize_schedule(s) for s in sched_svc.get_weekly_schedule(uid)],
                "upcoming_deadlines": [
                    _serialize_assignment(a) for a in assign_svc.get_upcoming_deadlines(uid, days=7)
                ],
                "connections": _connection_meta(uid),
            },
        )
    )
    registry.register(
        ToolDefinition(
            name="get_academic_overview",
            description="Tổng quan học tập: số học phần, buổi học sắp tới, bài tập chưa nộp và quá hạn.",
            input_model=EmptyInput,
            handler=lambda uid, _a: {
                "subjects_count": len(subj_svc.list_subjects(uid)),
                "next_class": (
                    _serialize_schedule(nxt) if (nxt := sched_svc.get_next_class(uid)) else None
                ),
                "pending_assignments_count": len(assign_svc.get_pending_assignments(uid)),
                "overdue_assignments_count": len(assign_svc.get_overdue_assignments(uid)),
                "reminders_count": len(rem_svc.list_reminders(uid, only_enabled=True)),
                "connections": _connection_meta(uid),
            },
        )
    )

    return registry
