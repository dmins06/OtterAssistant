from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SQLEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
from app.models.enums import CourseMappingStatus, SessionPeriod, SubmissionStatus


class Subject(Base, TimestampMixin):
    """Học phần từ Portal UTH (Source of Truth cho danh sách học phần)."""

    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("user_id", "subject_code", name="uq_subjects_user_subject_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lecturer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    syllabus_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    lms_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    schedules: Mapped[list["Schedule"]] = relationship(back_populates="subject")
    lms_courses: Mapped[list["LMSCourse"]] = relationship(back_populates="subject")
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="subject")


class LMSCourse(Base, TimestampMixin):
    """Khóa học trên UTH LMS — liên kết có kiểm chứng với Subject (không tự đoán sai)."""

    __tablename__ = "lms_courses"
    __table_args__ = (
        UniqueConstraint("user_id", "external_course_id", name="uq_lms_courses_user_ext_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_course_id: Mapped[str] = mapped_column(String(128), nullable=False)
    course_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    course_name: Mapped[str] = mapped_column(String(255), nullable=False)
    course_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    mapping_status: Mapped[CourseMappingStatus] = mapped_column(
        SQLEnum(CourseMappingStatus, native_enum=False, length=16),
        default=CourseMappingStatus.UNMAPPED,
        nullable=False,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subject: Mapped["Subject | None"] = relationship(back_populates="lms_courses")
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="lms_course")


class Schedule(Base, TimestampMixin):
    """
    Thời khóa biểu Portal UTH.
    Phân biệt rõ 3 trường độc lập theo giao diện thực tế portal.ut.edu.vn/calendar:
      - session: Buổi học (Sáng / Chiều / Tối)
      - class_session: Ca học (Ca 1, Ca 2, Ca 3, Ca 4...)
      - period_start / period_end: Tiết học (ví dụ Tiết 4-6, Tiết 7-9, Tiết 10-12)
    """

    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint("user_id", "source_event_id", name="uq_schedules_user_source_event"),
        Index("ix_schedules_user_date", "user_id", "class_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    class_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 2=Thứ 2 .. 7=Thứ 7, 8=Chủ nhật
    session: Mapped[SessionPeriod] = mapped_column(
        SQLEnum(SessionPeriod, native_enum=False, length=16), nullable=False
    )
    class_session: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "Ca 2", "Ca 3"
    period_start: Mapped[int] = mapped_column(Integer, nullable=False)  # e.g. 7
    period_end: Mapped[int] = mapped_column(Integer, nullable=False)  # e.g. 9
    start_time: Mapped[time] = mapped_column(Time, nullable=False)  # e.g. 12:10
    end_time: Mapped[time] = mapped_column(Time, nullable=False)  # e.g. 14:40
    room: Mapped[str | None] = mapped_column(String(128), nullable=True)  # e.g. "C007", "Hội Trường Hội Nhập"
    campus: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "CS1"
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "P.Thạnh Mỹ Tây, TP.HCM"
    raw_room_text: Mapped[str | None] = mapped_column(String(512), nullable=True)
    syllabus_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    lms_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subject: Mapped["Subject"] = relationship(back_populates="schedules")


class Assignment(Base, TimestampMixin):
    """Bài tập và deadline đồng bộ từ UTH LMS."""

    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "lms_course_id",
            "external_assignment_id",
            name="uq_assignments_user_course_ext_id",
        ),
        Index("ix_assignments_user_due_at", "user_id", "due_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    lms_course_id: Mapped[int] = mapped_column(
        ForeignKey("lms_courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_assignment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    submission_status: Mapped[SubmissionStatus] = mapped_column(
        SQLEnum(SubmissionStatus, native_enum=False, length=24),
        default=SubmissionStatus.UNKNOWN,
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grade: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignment_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subject: Mapped["Subject | None"] = relationship(back_populates="assignments")
    lms_course: Mapped["LMSCourse"] = relationship(back_populates="assignments")
