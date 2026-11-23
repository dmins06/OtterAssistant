from datetime import date, datetime, time, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.timezone import get_app_tz, utc_now
from app.integrations.telegram.client import MockTelegramBotClient
from app.models.academic import LMSCourse
from app.models.enums import (
    CourseMappingStatus,
    NotificationStatus,
    NotificationType,
    SessionPeriod,
    SubmissionStatus,
)
from app.models.user import AccountLinking, User
from app.notifications.service import NotificationService
from app.scheduler.jobs import (
    run_assignment_reminders,
    run_class_reminders,
    run_custom_reminders,
    run_daily_summary,
    run_weekly_summary,
)
from app.scheduler.manager import create_scheduler
from app.schemas.assignment import AssignmentUpsert
from app.schemas.reminder import ReminderCreate
from app.schemas.schedule import ScheduleUpsert
from app.schemas.subject import SubjectUpsert
from app.services.assignment_service import AssignmentService
from app.services.reminder_service import ReminderService
from app.services.schedule_service import ScheduleService
from app.services.subject_service import SubjectService
from tests.conftest import auth_headers


def _link_user_telegram(db: Session, user_id: int, chat_id: str) -> None:
    db.add(
        AccountLinking(
            user_id=user_id,
            provider="telegram",
            external_user_id=chat_id,
            is_active=True,
            last_interaction_at=utc_now(),
        )
    )
    db.commit()


def test_notification_idempotency_prevents_duplicates_and_handles_blocked_bot(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, user_b = two_users
    _link_user_telegram(db_session, user_a.id, "tg-chat-a")
    _link_user_telegram(db_session, user_b.id, "tg-chat-blocked")

    tg = MockTelegramBotClient(blocked_chat_ids={"tg-chat-blocked"})
    svc = NotificationService(db_session, tg)
    sched_time = datetime(2026, 12, 12, 2, 25, tzinfo=timezone.utc)

    # First send succeeds
    r1 = svc.send_notification(
        user_a.id, "schedule:1", NotificationType.UPCOMING_CLASS, sched_time, "Sắp học Học sâu!"
    )
    assert r1.sent is True
    assert r1.skipped_duplicate is False
    assert r1.status == NotificationStatus.SENT

    # Duplicate send with same (user, event, type, scheduled_for) is skipped
    r2 = svc.send_notification(
        user_a.id, "schedule:1", NotificationType.UPCOMING_CLASS, sched_time, "Sắp học Học sâu!"
    )
    assert r2.sent is False
    assert r2.skipped_duplicate is True
    assert len(tg.sent_messages) == 1

    # User B blocked bot -> recorded as POLICY_RESTRICTED (never crashes)
    r_b = svc.send_notification(
        user_b.id, "schedule:2", NotificationType.UPCOMING_CLASS, sched_time, "Test"
    )
    assert r_b.sent is False
    assert r_b.status == NotificationStatus.POLICY_RESTRICTED
    assert len(tg.sent_messages) == 1


def test_scheduler_jobs_class_assignment_custom_and_summaries(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    _link_user_telegram(db_session, user_a.id, "tg-chat-a")

    subj = SubjectService(db_session).upsert_subject(
        user_a.id,
        SubjectUpsert(subject_code="012012710505", name="Học sâu"),
    )
    ScheduleService(db_session).upsert_schedule(
        user_a.id,
        ScheduleUpsert(
            subject_id=subj.id,
            source_event_id="evt-sched-notif",
            class_date=date(2026, 12, 12),
            weekday=7,
            session=SessionPeriod.MORNING,
            class_session="Ca 2",
            period_start=4,
            period_end=6,
            start_time=time(9, 25),
            end_time=time(11, 55),
            room="C007",
            campus="CS1",
        ),
    )

    course = LMSCourse(
        user_id=user_a.id,
        subject_id=subj.id,
        external_course_id="505",
        course_name="Học sâu",
        mapping_status=CourseMappingStatus.MAPPED,
    )
    db_session.add(course)
    db_session.commit()
    db_session.refresh(course)

    tz = get_app_tz()
    now_local = datetime(2026, 12, 12, 9, 5, tzinfo=tz)
    now_utc = now_local.astimezone(timezone.utc)

    AssignmentService(db_session).upsert_assignment(
        user_a.id,
        AssignmentUpsert(
            lms_course_id=course.id,
            external_assignment_id="a-due-1d",
            title="Nộp bài tập CNN",
            due_at=(now_local + timedelta(days=1)).astimezone(timezone.utc),
            submission_status=SubmissionStatus.NOT_SUBMITTED,
        ),
    )

    ReminderService(db_session).create_reminder(
        user_a.id,
        ReminderCreate(
            title="Mang laptop lên lớp",
            remind_at=now_utc - timedelta(minutes=1),
        ),
    )

    tg = MockTelegramBotClient()
    notif_svc = NotificationService(db_session, tg)

    assert run_class_reminders(db_session, notif_svc, now_utc=now_utc) == 1
    assert run_class_reminders(db_session, notif_svc, now_utc=now_utc) == 0
    assert run_assignment_reminders(db_session, notif_svc, now_utc=now_utc) == 1
    assert run_custom_reminders(db_session, notif_svc, now_utc=now_utc) == 1
    assert run_daily_summary(db_session, notif_svc, now_utc=now_utc) == 1
    assert run_weekly_summary(db_session, notif_svc, now_utc=now_utc) == 1
    assert len(tg.sent_messages) == 5

    scheduler = create_scheduler()
    job_ids = {j.id for j in scheduler.get_jobs()}
    assert job_ids == {
        "class_reminders",
        "assignment_reminders",
        "custom_reminders",
        "daily_summary",
        "weekly_summary",
        "portal_semester_sync",
    }


def test_preferences_and_notification_logs_api(
    client: TestClient,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    get_resp = client.get("/api/v1/preferences", headers=auth_headers(user_a))
    assert get_resp.status_code == 200
    assert get_resp.json()["class_reminder_minutes"] == 30

    patch_resp = client.patch(
        "/api/v1/preferences",
        headers=auth_headers(user_a),
        json={"class_reminder_minutes": 15, "daily_summary_time": "06:30"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["class_reminder_minutes"] == 15
    assert patch_resp.json()["daily_summary_time"] == "06:30"

    logs_resp = client.get("/api/v1/notifications/logs", headers=auth_headers(user_a))
    assert logs_resp.status_code == 200
    assert isinstance(logs_resp.json(), list)
