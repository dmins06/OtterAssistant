from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import ensure_utc, get_app_tz, utc_now
from app.models.enums import NotificationType, SubmissionStatus
from app.models.notification import Reminder
from app.models.user import User, UserPreference
from app.notifications.service import NotificationService
from app.services.assignment_service import AssignmentService
from app.services.schedule_service import ScheduleService


def _get_active_users_with_prefs(db: Session) -> list[tuple[User, UserPreference | None]]:
    users = list(db.scalars(select(User).where(User.is_active.is_(True))).all())
    result: list[tuple[User, UserPreference | None]] = []
    for u in users:
        pref = db.scalar(select(UserPreference).where(UserPreference.user_id == u.id))
        result.append((u, pref))
    return result


def run_class_reminders(
    db: Session,
    notification_service: NotificationService,
    now_utc: datetime | None = None,
) -> int:
    """Remind students before upcoming classes based on `class_reminder_minutes` (default 30m)."""
    now = ensure_utc(now_utc) or utc_now()
    tz = get_app_tz()
    now_local = now.astimezone(tz)
    sched_svc = ScheduleService(db)
    sent_count = 0

    for user, pref in _get_active_users_with_prefs(db):
        lead_minutes = pref.class_reminder_minutes if pref else 30
        today_classes = sched_svc.get_schedule_by_date(user.id, now_local.date())
        for cls in today_classes:
            start_local = datetime.combine(cls.class_date, cls.start_time, tzinfo=tz)
            minutes_until = (start_local - now_local).total_seconds() / 60.0
            if 0 < minutes_until <= lead_minutes:
                subject_name = cls.subject.name if cls.subject else "Học phần"
                room_str = f"{cls.room or 'Chưa rõ phòng'}" + (f" - {cls.campus}" if cls.campus else "")
                msg = (
                    f"[Nhắc lịch học] Sắp đến giờ môn {subject_name}!\n"
                    f"- Thời gian: {cls.session.value}, {cls.class_session}, "
                    f"Tiết {cls.period_start}-{cls.period_end} ({cls.start_time.strftime('%H:%M')} - {cls.end_time.strftime('%H:%M')})\n"
                    f"- Phòng: {room_str}"
                )
                res = notification_service.send_notification(
                    user_id=user.id,
                    event_id=f"schedule:{cls.id}",
                    notification_type=NotificationType.UPCOMING_CLASS,
                    scheduled_for=start_local.astimezone(timezone.utc),
                    message_text=msg,
                )
                if res.sent:
                    sent_count += 1
    return sent_count


def run_assignment_reminders(
    db: Session,
    notification_service: NotificationService,
    now_utc: datetime | None = None,
) -> int:
    """Remind students before assignment deadlines (default: 3 days, 1 day, and due day)."""
    now = ensure_utc(now_utc) or utc_now()
    tz = get_app_tz()
    today_local = now.astimezone(tz).date()
    assign_svc = AssignmentService(db)
    sent_count = 0

    for user, pref in _get_active_users_with_prefs(db):
        raw_days = pref.assignment_reminder_days if pref else "3,1,0"
        target_days = {
            int(part.strip())
            for part in raw_days.split(",")
            if part.strip().isdigit()
        }
        pending = assign_svc.list_assignments(user.id)
        for a in pending:
            if a.submission_status in (SubmissionStatus.SUBMITTED, SubmissionStatus.GRADED):
                continue
            if a.due_at is None:
                continue
            due_utc = ensure_utc(a.due_at)
            assert due_utc is not None
            due_local = due_utc.astimezone(tz)
            days_left = (due_local.date() - today_local).days
            if days_left in target_days:
                label = "HÔM NAY" if days_left == 0 else f"còn {days_left} ngày"
                msg = (
                    f"[Nhắc deadline - {label}] {a.title}\n"
                    f"- Hạn nộp: {due_local.strftime('%d/%m/%Y %H:%M')}\n"
                    f"- Trạng thái: {a.submission_status.value}"
                )
                res = notification_service.send_notification(
                    user_id=user.id,
                    event_id=f"assignment:{a.id}:d{days_left}",
                    notification_type=NotificationType.UPCOMING_DEADLINE,
                    scheduled_for=datetime(
                        today_local.year, today_local.month, today_local.day, 0, 0, tzinfo=tz
                    ).astimezone(timezone.utc),
                    message_text=msg,
                )
                if res.sent:
                    sent_count += 1
    return sent_count


def run_custom_reminders(
    db: Session,
    notification_service: NotificationService,
    now_utc: datetime | None = None,
) -> int:
    """Dispatch enabled custom reminders whose `remind_at` has arrived."""
    now = ensure_utc(now_utc) or utc_now()
    due_reminders = list(
        db.scalars(
            select(Reminder).where(
                Reminder.is_enabled.is_(True),
                Reminder.is_sent.is_(False),
                Reminder.remind_at <= now,
            )
        ).all()
    )
    sent_count = 0
    for rem in due_reminders:
        msg = f"[Nhắc nhở] {rem.title}" + (f"\n{rem.notes}" if rem.notes else "")
        res = notification_service.send_notification(
            user_id=rem.user_id,
            event_id=f"reminder:{rem.id}",
            notification_type=NotificationType.CUSTOM_REMINDER,
            scheduled_for=rem.remind_at,
            message_text=msg,
        )
        rem.is_sent = True
        db.commit()
        if res.sent:
            sent_count += 1
    return sent_count


def run_daily_summary(
    db: Session,
    notification_service: NotificationService,
    now_utc: datetime | None = None,
) -> int:
    now = ensure_utc(now_utc) or utc_now()
    tz = get_app_tz()
    today = now.astimezone(tz).date()
    sched_svc = ScheduleService(db)
    assign_svc = AssignmentService(db)
    sent_count = 0

    for user, pref in _get_active_users_with_prefs(db):
        if pref and not pref.daily_summary_enabled:
            continue
        classes = sched_svc.get_schedule_by_date(user.id, today)
        deadlines = assign_svc.get_deadlines_for_date(user.id, today)
        overdue = assign_svc.get_overdue_assignments(user.id, now_utc=now)

        lines = [f"[Tổng hợp ngày {today.strftime('%d/%m/%Y')}] Chào {user.full_name}!"]
        if classes:
            lines.append(f"- Lịch học hôm nay ({len(classes)} buổi):")
            for c in classes:
                name = c.subject.name if c.subject else "Học phần"
                lines.append(
                    f"  • {name} ({c.class_session}, Tiết {c.period_start}-{c.period_end}, "
                    f"{c.start_time.strftime('%H:%M')}-{c.end_time.strftime('%H:%M')}, Phòng {c.room or '?'})"
                )
        else:
            lines.append("- Hôm nay bạn không có lịch học.")

        lines.append(f"- Deadline hôm nay: {len(deadlines)} | Bài tập quá hạn: {len(overdue)}")
        res = notification_service.send_notification(
            user_id=user.id,
            event_id=f"daily:{today.isoformat()}",
            notification_type=NotificationType.DAILY_SUMMARY,
            scheduled_for=datetime(today.year, today.month, today.day, 0, 0, tzinfo=tz).astimezone(timezone.utc),
            message_text="\n".join(lines),
        )
        if res.sent:
            sent_count += 1
    return sent_count


def run_weekly_summary(
    db: Session,
    notification_service: NotificationService,
    now_utc: datetime | None = None,
) -> int:
    now = ensure_utc(now_utc) or utc_now()
    tz = get_app_tz()
    today = now.astimezone(tz).date()
    monday = today - timedelta(days=today.weekday())
    sched_svc = ScheduleService(db)
    assign_svc = AssignmentService(db)
    sent_count = 0

    for user, pref in _get_active_users_with_prefs(db):
        if pref and not pref.weekly_summary_enabled:
            continue
        classes = sched_svc.get_weekly_schedule(user.id, reference_date=today)
        upcoming = assign_svc.get_upcoming_deadlines(user.id, days=7, now_utc=now)
        msg = (
            f"[Tổng hợp tuần {monday.strftime('%d/%m/%Y')}] Chào {user.full_name}!\n"
            f"- Tổng số buổi học trong tuần: {len(classes)}\n"
            f"- Bài tập sắp đến hạn trong 7 ngày tới: {len(upcoming)}"
        )
        res = notification_service.send_notification(
            user_id=user.id,
            event_id=f"weekly:{monday.isoformat()}",
            notification_type=NotificationType.WEEKLY_SUMMARY,
            scheduled_for=datetime(monday.year, monday.month, monday.day, 0, 0, tzinfo=tz).astimezone(timezone.utc),
            message_text=msg,
        )
        if res.sent:
            sent_count += 1
    return sent_count
