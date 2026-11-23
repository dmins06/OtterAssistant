from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Protocol

import httpx

from app.core.config import get_settings
from app.integrations.lms.authentication import LMSCredentialBundle
from app.integrations.lms.exceptions import (
    LMSAuthRequiredError,
    LMSChallengeBlockedError,
    LMSIntegrationError,
)
from app.models.enums import SubmissionStatus


@dataclass(frozen=True)
class LMSCourseItem:
    external_course_id: str
    course_name: str
    course_code: str | None = None
    course_url: str | None = None


@dataclass(frozen=True)
class LMSAssignmentItem:
    external_course_id: str
    external_assignment_id: str
    title: str
    description: str | None = None
    created_at_source: datetime | None = None
    due_at: datetime | None = None
    submission_status: SubmissionStatus = SubmissionStatus.UNKNOWN
    submitted_at: datetime | None = None
    grade: float | None = None
    feedback: str | None = None
    attachments: list[dict[str, str]] = field(default_factory=list)
    assignment_url: str | None = None


class LMSProvider(Protocol):
    is_mock: bool

    def fetch_courses_and_assignments(
        self,
        credentials: LMSCredentialBundle,
    ) -> tuple[list[LMSCourseItem], list[LMSAssignmentItem]]:
        ...


def _check_cloudflare_challenge(response: httpx.Response) -> None:
    if response.status_code == 403 and (
        response.headers.get("cf-mitigated", "").lower() == "challenge"
        or "challenges.cloudflare.com" in response.text
    ):
        raise LMSChallengeBlockedError()


def _parse_ics_datetime(raw_value: str) -> datetime | None:
    """Parse RFC 5545 DTSTART/DTEND formats (UTC Z or YYYYMMDDTHHMMSS)."""
    val = raw_value.strip()
    if val.endswith("Z"):
        try:
            return datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_ical_feed(ics_text: str) -> tuple[list[LMSCourseItem], list[LMSAssignmentItem]]:
    """
    Parse a Moodle Calendar iCal (.ics) export feed into LMS courses and assignment deadlines.
    Never fabricates submission status — defaults to SubmissionStatus.UNKNOWN.
    """
    # Unfold folded RFC 5545 lines (CRLF/LF followed by single space or tab)
    unfolded = re.sub(r"\r?\n[ \t]", "", ics_text)
    lines = unfolded.splitlines()

    courses_by_id: dict[str, LMSCourseItem] = {}
    assignments: list[LMSAssignmentItem] = []

    in_event = False
    event_fields: dict[str, str] = {}

    for line in lines:
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            in_event = True
            event_fields = {}
            continue
        if stripped == "END:VEVENT" and in_event:
            in_event = False
            uid = event_fields.get("UID", "").strip()
            summary = event_fields.get("SUMMARY", "").strip()
            if not uid or not summary:
                continue

            category = event_fields.get("CATEGORIES", "").strip() or "Unassigned LMS Course"
            course_ext_id = f"ical:{category}"
            if course_ext_id not in courses_by_id:
                courses_by_id[course_ext_id] = LMSCourseItem(
                    external_course_id=course_ext_id,
                    course_code=category if re.fullmatch(r"[A-Za-z0-9_-]{5,24}", category) else None,
                    course_name=category,
                )

            due_raw = event_fields.get("DTEND") or event_fields.get("DTSTART")
            due_at = _parse_ics_datetime(due_raw) if due_raw else None
            description = event_fields.get("DESCRIPTION")
            if description:
                description = description.replace("\\n", "\n").replace("\\,", ",")

            assignments.append(
                LMSAssignmentItem(
                    external_course_id=course_ext_id,
                    external_assignment_id=uid,
                    title=summary,
                    description=description,
                    due_at=due_at,
                    submission_status=(
                        SubmissionStatus.NO_DEADLINE if due_at is None else SubmissionStatus.UNKNOWN
                    ),
                    assignment_url=event_fields.get("URL"),
                )
            )
            continue

        if in_event and ":" in stripped:
            key_part, value_part = stripped.split(":", 1)
            base_key = key_part.split(";", 1)[0].upper()
            event_fields[base_key] = value_part

    return list(courses_by_id.values()), assignments


class ICalFeedLMSClient:
    """Authorized read-only Moodle Calendar iCal (.ics) feed client."""

    is_mock: bool = False

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_courses_and_assignments(
        self,
        credentials: LMSCredentialBundle,
    ) -> tuple[list[LMSCourseItem], list[LMSAssignmentItem]]:
        if not credentials.ical_url:
            raise LMSAuthRequiredError("iCal feed URL is missing.", error_code="ICAL_URL_MISSING")
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(credentials.ical_url)
            _check_cloudflare_challenge(response)
            if response.status_code in (401, 403):
                raise LMSAuthRequiredError(
                    f"iCal feed rejected access (HTTP {response.status_code}).",
                    error_code=f"LMS_HTTP_{response.status_code}",
                )
            if response.status_code >= 400:
                raise LMSIntegrationError(
                    f"iCal feed returned HTTP {response.status_code}.",
                    error_code=f"LMS_HTTP_{response.status_code}",
                )
            return parse_ical_feed(response.text)


class MoodleWebServiceClient:
    """
    Official Moodle REST Web Services client (requires authorized wstoken).
    Detects Cloudflare anti-bot challenge cleanly and never attempts to bypass it.
    """

    is_mock: bool = False

    def __init__(self, base_url: str | None = None, timeout_seconds: float = 15.0) -> None:
        self.base_url = (base_url or get_settings().lms_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _call(self, client: httpx.Client, wstoken: str, wsfunction: str, extra: dict[str, Any] | None = None) -> Any:
        params: dict[str, Any] = {
            "wstoken": wstoken,
            "wsfunction": wsfunction,
            "moodlewsrestformat": "json",
        }
        if extra:
            params.update(extra)
        response = client.get(f"{self.base_url}/webservice/rest/server.php", params=params)
        _check_cloudflare_challenge(response)
        if response.status_code >= 400:
            raise LMSIntegrationError(
                f"Moodle Web Service returned HTTP {response.status_code}.",
                error_code=f"LMS_HTTP_{response.status_code}",
            )
        data = response.json()
        if isinstance(data, dict) and data.get("exception"):
            err_code = str(data.get("errorcode") or "MOODLE_WS_ERROR")
            if err_code in {"invalidtoken", "accessexception"}:
                raise LMSAuthRequiredError(str(data.get("message", "Invalid Moodle token")), error_code=err_code)
            raise LMSIntegrationError(str(data.get("message", "Moodle API error")), error_code=err_code)
        return data

    def fetch_courses_and_assignments(
        self,
        credentials: LMSCredentialBundle,
    ) -> tuple[list[LMSCourseItem], list[LMSAssignmentItem]]:
        if not credentials.wstoken:
            raise LMSAuthRequiredError("Moodle wstoken is missing.", error_code="WSTOKEN_MISSING")

        with httpx.Client(timeout=self.timeout_seconds) as client:
            site_info = self._call(client, credentials.wstoken, "core_webservice_get_site_info")
            moodle_uid = site_info["userid"]
            raw_courses = self._call(
                client,
                credentials.wstoken,
                "core_enrol_get_users_courses",
                {"userid": moodle_uid},
            )

            courses: list[LMSCourseItem] = []
            course_ids: list[int] = []
            for c in raw_courses or []:
                cid = int(c["id"])
                course_ids.append(cid)
                courses.append(
                    LMSCourseItem(
                        external_course_id=str(cid),
                        course_code=c.get("idnumber") or c.get("shortname"),
                        course_name=str(c.get("fullname") or c.get("shortname") or f"Course {cid}"),
                        course_url=f"{self.base_url}/course/view.php?id={cid}",
                    )
                )

            if not course_ids:
                return courses, []

            extra_ids = {f"courseids[{idx}]": cid for idx, cid in enumerate(course_ids)}
            assign_resp = self._call(client, credentials.wstoken, "mod_assign_get_assignments", extra_ids)
            assignments: list[LMSAssignmentItem] = []
            for c_block in assign_resp.get("courses", []):
                ext_course_id = str(c_block["id"])
                for a in c_block.get("assignments", []):
                    due_ts = a.get("duedate") or 0
                    due_at = datetime.fromtimestamp(due_ts, tz=timezone.utc) if due_ts else None
                    cmid = a.get("cmid")
                    assignments.append(
                        LMSAssignmentItem(
                            external_course_id=ext_course_id,
                            external_assignment_id=str(a["id"]),
                            title=str(a["name"]),
                            description=a.get("intro"),
                            due_at=due_at,
                            submission_status=(
                                SubmissionStatus.NO_DEADLINE if due_at is None else SubmissionStatus.UNKNOWN
                            ),
                            assignment_url=f"{self.base_url}/mod/assign/view.php?id={cmid}" if cmid else None,
                        )
                    )
            return courses, assignments


class MockLMSClient:
    """Explicit mock LMS client for offline development and automated tests."""

    is_mock: bool = True

    def __init__(
        self,
        courses: list[LMSCourseItem] | None = None,
        assignments: list[LMSAssignmentItem] | None = None,
    ) -> None:
        self._courses = courses
        self._assignments = assignments

    def fetch_courses_and_assignments(
        self,
        credentials: LMSCredentialBundle,  # noqa: ARG002
    ) -> tuple[list[LMSCourseItem], list[LMSAssignmentItem]]:
        if self._courses is not None and self._assignments is not None:
            return self._courses, self._assignments

        now = datetime.now(timezone.utc)
        courses = [
            LMSCourseItem(
                external_course_id="505",
                course_code="012012710505",
                course_name="Học sâu",
                course_url="https://courses.ut.edu.vn/course/view.php?id=505",
            ),
            LMSCourseItem(
                external_course_id="606",
                course_code="012012710606",
                course_name="Trực quan hóa dữ liệu",
                course_url="https://courses.ut.edu.vn/course/view.php?id=606",
            ),
        ]
        assignments = [
            LMSAssignmentItem(
                external_course_id="505",
                external_assignment_id="assign-dl-1",
                title="Bài tập thực hành CNN",
                description="Huấn luyện mô hình phân loại ảnh và nộp báo cáo PDF.",
                due_at=now + timedelta(days=2),
                submission_status=SubmissionStatus.NOT_SUBMITTED,
                assignment_url="https://courses.ut.edu.vn/mod/assign/view.php?id=1001",
            ),
            LMSAssignmentItem(
                external_course_id="606",
                external_assignment_id="assign-dv-1",
                title="Dashboard phân tích dữ liệu giao thông",
                description="Trình bày 3 biểu đồ tương tác.",
                due_at=now + timedelta(days=5),
                submission_status=SubmissionStatus.SUBMITTED,
                submitted_at=now - timedelta(hours=3),
                assignment_url="https://courses.ut.edu.vn/mod/assign/view.php?id=1002",
            ),
        ]
        return courses, assignments
