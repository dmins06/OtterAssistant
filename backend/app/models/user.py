from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    student_code: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    preferences: Mapped["UserPreference | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    account_linkings: Mapped[list["AccountLinking"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserPreference(Base, TimestampMixin):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Ho_Chi_Minh", nullable=False)
    class_reminder_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    assignment_reminder_days: Mapped[str] = mapped_column(String(64), default="3,1,0", nullable=False)
    daily_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    daily_summary_time: Mapped[str] = mapped_column(String(8), default="07:00", nullable=False)
    weekly_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    weekly_summary_day: Mapped[int] = mapped_column(Integer, default=6, nullable=False)  # 6 = Sunday

    user: Mapped["User"] = relationship(back_populates="preferences")


class AccountLinking(Base, TimestampMixin):
    __tablename__ = "account_linkings"
    __table_args__ = (
        UniqueConstraint("provider", "external_user_id", name="uq_account_linkings_provider_ext_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="telegram", nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="account_linkings")
