from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.academic import LMSCourse, Subject
from app.models.enums import CourseMappingStatus


def _extract_moodle_course_id(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "id" in qs and qs["id"]:
        return qs["id"][0].strip()
    return None


class CourseMappingService:
    """
    Links LMSCourse records to Portal Subject records strictly per Section 3.3:
      Priority 1: Official subject_code match
      Priority 2: Official lms_url / Moodle course ID match
      Never maps purely by fuzzy/similar course title.
      Marks UNMAPPED (0 matches) or AMBIGUOUS (>1 matches) rather than guessing wrong.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve_mapping(
        self,
        user_id: int,
        lms_course: LMSCourse,
    ) -> tuple[int | None, CourseMappingStatus]:
        subjects = list(
            self.db.scalars(select(Subject).where(Subject.user_id == user_id)).all()
        )
        if not subjects:
            return None, CourseMappingStatus.UNMAPPED

        # Priority 1: Official Subject Code match
        if lms_course.course_code:
            code_norm = lms_course.course_code.strip().lower()
            code_matches = [
                s for s in subjects if s.subject_code.strip().lower() == code_norm
            ]
            if len(code_matches) == 1:
                return code_matches[0].id, CourseMappingStatus.MAPPED
            if len(code_matches) > 1:
                return None, CourseMappingStatus.AMBIGUOUS

        # Priority 2: Official LMS URL or Moodle course ID match
        ext_id = lms_course.external_course_id.strip()
        lms_url_norm = lms_course.course_url.rstrip("/").lower() if lms_course.course_url else None
        url_matches: list[Subject] = []
        for s in subjects:
            if not s.lms_url:
                continue
            s_url_norm = s.lms_url.rstrip("/").lower()
            if lms_url_norm and s_url_norm == lms_url_norm:
                url_matches.append(s)
                continue
            moodle_id = _extract_moodle_course_id(s.lms_url)
            if moodle_id and moodle_id == ext_id:
                url_matches.append(s)

        if len(url_matches) == 1:
            return url_matches[0].id, CourseMappingStatus.MAPPED
        if len(url_matches) > 1:
            return None, CourseMappingStatus.AMBIGUOUS

        # Do NOT match by course title alone — mark UNMAPPED
        return None, CourseMappingStatus.UNMAPPED
