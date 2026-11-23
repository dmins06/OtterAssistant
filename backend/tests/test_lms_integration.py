from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.lms import get_lms_auth_manager
from app.core.security import SecretEncryptor
from app.integrations.lms.assignment_sync import LMSAssignmentSyncService
from app.integrations.lms.authentication import LMSAuthManager
from app.integrations.lms.client import (
    LMSAssignmentItem,
    LMSCourseItem,
    MockLMSClient,
    parse_ical_feed,
)
from app.integrations.lms.course_mapping import CourseMappingService
from app.integrations.lms.exceptions import LMSChallengeBlockedError
from app.models.academic import Assignment, LMSCourse
from app.models.connection import LMSConnection, SyncLog
from app.models.enums import (
    ConnectionStatus,
    CourseMappingStatus,
    SubmissionStatus,
    SyncStatus,
)
from app.models.user import User
from app.schemas.subject import SubjectUpsert
from app.services.subject_service import SubjectService
from tests.conftest import auth_headers


def _make_encryptor() -> SecretEncryptor:
    return SecretEncryptor(key=Fernet.generate_key().decode("utf-8"))


def test_ical_feed_parser_extracts_deadlines_and_keeps_unknown_status() -> None:
    sample_ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:assign-901@courses.ut.edu.vn
SUMMARY:Nộp báo cáo Học sâu
DESCRIPTION:Nộp file PDF\\nKèm source code
CATEGORIES:012012710505
DTSTART:20261215T165900Z
DTEND:20261215T165900Z
URL:https://courses.ut.edu.vn/mod/assign/view.php?id=901
END:VEVENT
END:VCALENDAR
"""
    courses, assignments = parse_ical_feed(sample_ics)
    assert len(courses) == 1
    assert courses[0].course_code == "012012710505"
    assert len(assignments) == 1
    assert assignments[0].title == "Nộp báo cáo Học sâu"
    assert assignments[0].due_at == datetime(2026, 12, 15, 16, 59, 0, tzinfo=timezone.utc)
    # iCal feed does not expose submission state -> must be UNKNOWN, never fabricated
    assert assignments[0].submission_status == SubmissionStatus.UNKNOWN


def test_course_mapping_prioritizes_official_ids_and_marks_unmapped_instead_of_guessing(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    subj_svc = SubjectService(db_session)
    deep_learning = subj_svc.upsert_subject(
        user_a.id,
        SubjectUpsert(
            subject_code="012012710505",
            name="Học sâu",
            lms_url="https://courses.ut.edu.vn/course/view.php?id=505",
        ),
    )
    data_viz = subj_svc.upsert_subject(
        user_a.id,
        SubjectUpsert(
            subject_code="012012710606",
            name="Trực quan hóa dữ liệu",
            lms_url="https://courses.ut.edu.vn/course/view.php?id=606",
        ),
    )
    mapper = CourseMappingService(db_session)

    # 1. Match by official subject_code
    c_by_code = LMSCourse(
        user_id=user_a.id,
        external_course_id="999",
        course_code="012012710505",
        course_name="Deep Learning (EN)",
    )
    subj_id, status = mapper.resolve_mapping(user_a.id, c_by_code)
    assert (subj_id, status) == (deep_learning.id, CourseMappingStatus.MAPPED)

    # 2. Match by official LMS URL / Moodle id=
    c_by_url = LMSCourse(
        user_id=user_a.id,
        external_course_id="606",
        course_code=None,
        course_name="Data Visualization",
        course_url="https://courses.ut.edu.vn/course/view.php?id=606",
    )
    subj_id, status = mapper.resolve_mapping(user_a.id, c_by_url)
    assert (subj_id, status) == (data_viz.id, CourseMappingStatus.MAPPED)

    # 3. Same name only, no matching code or URL -> MUST mark UNMAPPED (never guess by name)
    c_name_only = LMSCourse(
        user_id=user_a.id,
        external_course_id="777",
        course_code=None,
        course_name="Học sâu",
        course_url="https://courses.ut.edu.vn/course/view.php?id=777",
    )
    subj_id, status = mapper.resolve_mapping(user_a.id, c_name_only)
    assert (subj_id, status) == (None, CourseMappingStatus.UNMAPPED)


def test_lms_sync_lifecycle_deadline_change_and_cloudflare_block_preserves_data(
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, _ = two_users
    SubjectService(db_session).upsert_subject(
        user_a.id,
        SubjectUpsert(
            subject_code="012012710505",
            name="Học sâu",
            lms_url="https://courses.ut.edu.vn/course/view.php?id=505",
        ),
    )

    auth_mgr = LMSAuthManager(db_session, _make_encryptor())
    auth_mgr.connect(user_a.id, "mock")

    now = datetime(2026, 11, 22, 8, 0, tzinfo=timezone.utc)
    courses = [
        LMSCourseItem(
            external_course_id="505",
            course_code="012012710505",
            course_name="Học sâu",
            course_url="https://courses.ut.edu.vn/course/view.php?id=505",
        )
    ]
    assignments_v1 = [
        LMSAssignmentItem(
            external_course_id="505",
            external_assignment_id="a1",
            title="Lab 1",
            due_at=now + timedelta(days=2),
            submission_status=SubmissionStatus.NOT_SUBMITTED,
        )
    ]

    sync_svc = LMSAssignmentSyncService(db_session, MockLMSClient(courses, assignments_v1), auth_mgr)
    res1 = sync_svc.sync_assignments(user_a.id)
    assert res1.status == SyncStatus.SUCCESS
    assert res1.courses_synced == 1
    assert res1.items_added == 1
    assert res1.deadlines_changed == 0

    # Sync v2: deadline shifts by +2 days
    assignments_v2 = [
        LMSAssignmentItem(
            external_course_id="505",
            external_assignment_id="a1",
            title="Lab 1",
            due_at=now + timedelta(days=4),
            submission_status=SubmissionStatus.NOT_SUBMITTED,
        )
    ]
    sync_svc_v2 = LMSAssignmentSyncService(db_session, MockLMSClient(courses, assignments_v2), auth_mgr)
    res2 = sync_svc_v2.sync_assignments(user_a.id)
    assert res2.status == SyncStatus.SUCCESS
    assert res2.items_added == 0
    assert res2.items_updated == 1
    assert res2.deadlines_changed == 1

    # Sync v3: Cloudflare challenge blocks LMS -> fails cleanly and PRESERVES existing assignments
    class BlockedLMSProvider(MockLMSClient):
        def fetch_courses_and_assignments(self, credentials):  # type: ignore[no-untyped-def]
            raise LMSChallengeBlockedError()

    sync_blocked = LMSAssignmentSyncService(db_session, BlockedLMSProvider(), auth_mgr)
    res_blocked = sync_blocked.sync_assignments(user_a.id)
    assert res_blocked.status == SyncStatus.FAILED
    assert res_blocked.error_code == "LMS_ANTI_BOT_CHALLENGE"

    preserved = list(
        db_session.scalars(
            select(Assignment).where(Assignment.user_id == user_a.id, Assignment.is_deleted.is_(False))
        ).all()
    )
    assert len(preserved) == 1
    assert auth_mgr.get_status(user_a.id).connection_status == ConnectionStatus.ERROR

    logs = list(db_session.scalars(select(SyncLog).where(SyncLog.user_id == user_a.id)).all())
    assert len(logs) == 3


def test_lms_api_encrypts_credentials_never_leaks_and_isolates_users(
    client: TestClient,
    db_session: Session,
    two_users: tuple[User, User],
) -> None:
    user_a, user_b = two_users
    enc = _make_encryptor()
    client.app.dependency_overrides[get_lms_auth_manager] = lambda: LMSAuthManager(db_session, enc)  # type: ignore[attr-defined]

    secret_token = "moodle-secret-wstoken-99999"
    resp = client.post(
        "/api/v1/integrations/lms/connect",
        headers=auth_headers(user_a),
        json={"integration_method": "moodle_ws", "wstoken": secret_token},
    )
    assert resp.status_code == 200
    assert secret_token not in resp.text
    assert resp.json()["connection_status"] == "AUTHENTICATED"
    assert resp.json()["integration_method"] == "moodle_ws"

    conn = db_session.scalar(select(LMSConnection).where(LMSConnection.user_id == user_a.id))
    assert conn is not None and conn.encrypted_credentials is not None
    assert secret_token not in conn.encrypted_credentials

    # User B remains disconnected
    resp_b = client.get("/api/v1/integrations/lms/status", headers=auth_headers(user_b))
    assert resp_b.status_code == 200
    assert resp_b.json()["connection_status"] == "DISCONNECTED"
