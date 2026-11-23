from datetime import date, datetime, time, timedelta, timezone

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models.academic import LMSCourse
from app.models.enums import (
    CourseMappingStatus,
    ReminderTargetType,
    SessionPeriod,
    SubmissionStatus,
)
from app.models.user import User
from app.schemas.assignment import AssignmentUpsert
from app.schemas.reminder import ReminderCreate, ReminderUpdate
from app.schemas.schedule import ScheduleUpsert
from app.schemas.subject import SubjectUpsert
from app.services.assignment_service import AssignmentService
from app.services.reminder_service import ReminderService
from app.services.schedule_service import ScheduleService
from app.services.subject_service import SubjectService
from tests.conftest import auth_headers


def test_all_14_tables_registered_and_alembic_head_valid() -> None:
    expected_tables = {
        "users",
        "user_preferences",
        "account_linkings",
        "portal_connections",
        "lms_connections",
        "subjects",
        "lms_courses",
        "schedules",
        "assignments",
        "reminders",
        "notification_logs",
        "sync_logs",
        "agent_conversations",
        "agent_memories",
    }
    assert expected_tables.issubset(set(Base.metadata.tables.keys()))

    alembic_cfg = Config("alembic.ini")
    script_dir = ScriptDirectory.from_config(alembic_cfg)
    assert script_dir.get_heads() == ["0001_initial_schema"]


def test_uth_schedule_structure_conflict_and_queries(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    subject_svc = SubjectService(db_session)
    schedule_svc = ScheduleService(db_session)

    deep_learning = subject_svc.upsert_subject(
        user_a.id,
        SubjectUpsert(
            subject_code="012012710505",
            name="Học sâu",
            syllabus_url="https://portal.ut.edu.vn/syllabus/012012710505",
            lms_url="https://courses.ut.edu.vn/course/view.php?id=505",
        ),
    )
    political_econ = subject_svc.upsert_subject(
        user_a.id,
        SubjectUpsert(
            subject_code="012000510605",
            name="Kinh tế chính trị Mác - Lênin",
        ),
    )

    # Real UTH Portal structure: Buổi (Sáng/Chiều), Ca (Ca 2/Ca 3), Tiết (4-6 / 7-9)
    schedule_svc.upsert_schedule(
        user_a.id,
        ScheduleUpsert(
            subject_id=deep_learning.id,
            source_event_id="uth-2026-12-12-012012710505-ca2",
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
            location="P.Thạnh Mỹ Tây, TP.HCM",
            raw_room_text="Phòng: C007 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)",
            lms_url="https://courses.ut.edu.vn/course/view.php?id=505",
        ),
    )
    schedule_svc.upsert_schedule(
        user_a.id,
        ScheduleUpsert(
            subject_id=political_econ.id,
            source_event_id="uth-2026-12-07-012000510605-ca3",
            class_date=date(2026, 12, 7),
            weekday=2,
            session=SessionPeriod.AFTERNOON,
            class_session="Ca 3",
            period_start=7,
            period_end=9,
            start_time=time(12, 10),
            end_time=time(14, 40),
            room="Hội Trường Hội Nhập",
            campus="CS1",
            location="P.Thạnh Mỹ Tây, TP.HCM",
        ),
    )

    # Weekly query (Mon 2026-12-07 -> Sun 2026-12-13)
    weekly = schedule_svc.get_weekly_schedule(user_a.id, reference_date=date(2026, 12, 10))
    assert len(weekly) == 2
    assert weekly[0].session == SessionPeriod.AFTERNOON
    assert weekly[0].class_session == "Ca 3"
    assert (weekly[0].period_start, weekly[0].period_end) == (7, 9)
    assert weekly[1].session == SessionPeriod.MORNING
    assert weekly[1].class_session == "Ca 2"
    assert (weekly[1].period_start, weekly[1].period_end) == (4, 6)

    # Empty day detection (Sunday 2026-12-13 has no classes)
    assert schedule_svc.is_empty_day(user_a.id, date(2026, 12, 13)) is True
    assert schedule_svc.is_empty_day(user_a.id, date(2026, 12, 12)) is False

    # Room lookup
    rooms = schedule_svc.find_rooms(user_a.id, "C007")
    assert len(rooms) == 1
    assert rooms[0].subject.name == "Học sâu"

    # Next class lookup
    next_cls = schedule_svc.get_next_class(
        user_a.id,
        now_local=datetime(2026, 12, 7, 10, 0, tzinfo=timezone(timedelta(hours=7))),
    )
    assert next_cls is not None
    assert next_cls.class_session == "Ca 3"

    # Add overlapping schedule on 2026-12-12 to verify conflict detection
    schedule_svc.upsert_schedule(
        user_a.id,
        ScheduleUpsert(
            subject_id=political_econ.id,
            source_event_id="uth-2026-12-12-conflict",
            class_date=date(2026, 12, 12),
            weekday=7,
            session=SessionPeriod.MORNING,
            class_session="Ca 2",
            period_start=5,
            period_end=6,
            start_time=time(10, 15),
            end_time=time(11, 55),
            room="B303",
            campus="CS1",
        ),
    )
    conflicts = schedule_svc.detect_conflicts(user_a.id, date(2026, 12, 7), date(2026, 12, 13))
    assert len(conflicts) == 1
    assert conflicts[0].class_date == date(2026, 12, 12)
    assert conflicts[0].overlap_start == time(10, 15)
    assert conflicts[0].overlap_end == time(11, 55)


def test_assignment_null_safe_upsert_deadline_change_and_statuses(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    subject = SubjectService(db_session).upsert_subject(
        user_a.id,
        SubjectUpsert(subject_code="012012710505", name="Học sâu"),
    )
    lms_course = LMSCourse(
        user_id=user_a.id,
        subject_id=subject.id,
        external_course_id="moodle-505",
        course_code="012012710505",
        course_name="Học sâu",
        mapping_status=CourseMappingStatus.MAPPED,
    )
    db_session.add(lms_course)
    db_session.commit()
    db_session.refresh(lms_course)

    svc = AssignmentService(db_session)
    now = datetime(2026, 11, 22, 8, 0, tzinfo=timezone.utc)

    # Initial sync
    res1 = svc.upsert_assignment(
        user_a.id,
        AssignmentUpsert(
            lms_course_id=lms_course.id,
            external_assignment_id="assign-1",
            title="Lab 1: CNN",
            description="Train CNN on CIFAR-10",
            due_at=now + timedelta(days=2),
            submission_status=SubmissionStatus.NOT_SUBMITTED,
            assignment_url="https://courses.ut.edu.vn/mod/assign/view.php?id=1",
        ),
    )
    assert res1.is_new is True
    assert res1.deadline_changed is False

    # Re-sync with omitted description (None) and shifted deadline -> must keep description & flag deadline_changed
    res2 = svc.upsert_assignment(
        user_a.id,
        AssignmentUpsert(
            lms_course_id=lms_course.id,
            external_assignment_id="assign-1",
            title="Lab 1: CNN (Extended)",
            description=None,
            due_at=now + timedelta(days=4),
            submission_status=SubmissionStatus.NOT_SUBMITTED,
        ),
    )
    assert res2.is_new is False
    assert res2.deadline_changed is True
    assert res2.assignment.description == "Train CNN on CIFAR-10"

    # Overdue + Submitted + Graded assignments
    svc.upsert_assignment(
        user_a.id,
        AssignmentUpsert(
            lms_course_id=lms_course.id,
            external_assignment_id="assign-overdue",
            title="Quiz 0",
            due_at=now - timedelta(days=1),
            submission_status=SubmissionStatus.OVERDUE,
        ),
    )
    svc.upsert_assignment(
        user_a.id,
        AssignmentUpsert(
            lms_course_id=lms_course.id,
            external_assignment_id="assign-graded",
            title="Lab 0",
            due_at=now - timedelta(days=3),
            submission_status=SubmissionStatus.GRADED,
            grade=9.5,
            feedback="Good work",
        ),
    )

    assert len(svc.get_upcoming_deadlines(user_a.id, days=7, now_utc=now)) == 1
    assert len(svc.get_overdue_assignments(user_a.id, now_utc=now)) == 1
    assert len(svc.get_submitted_assignments(user_a.id)) == 1
    assert len(svc.get_pending_assignments(user_a.id)) == 2


def test_strict_user_data_isolation_across_services_and_api(
    client: TestClient,
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, user_b = two_users

    # User A creates subject, schedule, reminder via API
    subj_resp = client.post(
        "/api/v1/subjects",
        headers=auth_headers(user_a),
        json={"subject_code": "012012710606", "name": "Trực quan hóa dữ liệu"},
    )
    assert subj_resp.status_code == 201
    subject_a_id = subj_resp.json()["id"]

    sched_resp = client.post(
        "/api/v1/schedules",
        headers=auth_headers(user_a),
        json={
            "subject_id": subject_a_id,
            "source_event_id": "evt-a-1",
            "class_date": "2026-12-08",
            "weekday": 3,
            "session": "Chiều",
            "class_session": "Ca 3",
            "period_start": 7,
            "period_end": 9,
            "start_time": "12:10:00",
            "end_time": "14:40:00",
            "room": "C002",
            "campus": "CS1",
        },
    )
    assert sched_resp.status_code == 201

    rem_resp = client.post(
        "/api/v1/reminders",
        headers=auth_headers(user_a),
        json={
            "target_type": ReminderTargetType.CUSTOM.value,
            "title": "Nộp bài Trực quan hóa",
            "remind_at": "2026-12-08T05:00:00Z",
        },
    )
    assert rem_resp.status_code == 201
    reminder_a_id = rem_resp.json()["id"]

    # User B MUST NOT see, modify, or delete any of User A's records
    assert client.get("/api/v1/subjects", headers=auth_headers(user_b)).json() == []
    assert client.get(f"/api/v1/subjects/{subject_a_id}", headers=auth_headers(user_b)).status_code == 404
    assert (
        client.get(
            "/api/v1/schedules/by-date?target_date=2026-12-08",
            headers=auth_headers(user_b),
        ).json()
        == []
    )
    assert client.get("/api/v1/reminders", headers=auth_headers(user_b)).json() == []
    assert (
        client.patch(
            f"/api/v1/reminders/{reminder_a_id}",
            headers=auth_headers(user_b),
            json={"title": "Hacked"},
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/reminders/{reminder_a_id}",
            headers=auth_headers(user_b),
        ).status_code
        == 404
    )

    # User B also cannot attach a schedule to User A's subject
    with pytest.raises(PermissionError):
        ScheduleService(db_session).upsert_schedule(
            user_b.id,
            ScheduleUpsert(
                subject_id=subject_a_id,
                source_event_id="evt-cross-tenant",
                class_date=date(2026, 12, 8),
                weekday=3,
                session=SessionPeriod.AFTERNOON,
                class_session="Ca 3",
                period_start=7,
                period_end=9,
                start_time=time(12, 10),
                end_time=time(14, 40),
            ),
        )

    # User A's own reminder update + delete works cleanly
    rem_svc = ReminderService(db_session)
    updated = rem_svc.update_reminder(
        user_a.id,
        reminder_a_id,
        ReminderUpdate(is_enabled=False, title="Updated reminder"),
    )
    assert updated is not None
    assert updated.is_enabled is False
    assert rem_svc.delete_reminder(user_a.id, reminder_a_id) is True
