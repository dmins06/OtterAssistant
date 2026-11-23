from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.core.timezone import local_today
from app.database.session import get_db
from app.models.academic import Schedule
from app.models.user import User
from app.schemas.schedule import ScheduleConflict, ScheduleResponse, ScheduleUpsert
from app.services.schedule_service import ScheduleService

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _to_response(item: Schedule) -> ScheduleResponse:
    return ScheduleResponse(
        id=item.id,
        subject_id=item.subject_id,
        subject_code=item.subject.subject_code if item.subject else None,
        subject_name=item.subject.name if item.subject else None,
        source_event_id=item.source_event_id,
        class_date=item.class_date,
        weekday=item.weekday,
        session=item.session,
        class_session=item.class_session,
        period_start=item.period_start,
        period_end=item.period_end,
        start_time=item.start_time,
        end_time=item.end_time,
        room=item.room,
        campus=item.campus,
        location=item.location,
        raw_room_text=item.raw_room_text,
        syllabus_url=item.syllabus_url,
        lms_url=item.lms_url,
        last_synced_at=item.last_synced_at,
    )


@router.get("/today", response_model=list[ScheduleResponse])
def get_today_schedule(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ScheduleResponse]:
    items = ScheduleService(db).get_today_schedule(current_user.id)
    return [_to_response(i) for i in items]


@router.get("/tomorrow", response_model=list[ScheduleResponse])
def get_tomorrow_schedule(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ScheduleResponse]:
    items = ScheduleService(db).get_tomorrow_schedule(current_user.id)
    return [_to_response(i) for i in items]


@router.get("/weekly", response_model=list[ScheduleResponse])
def get_weekly_schedule(
    ref_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ScheduleResponse]:
    items = ScheduleService(db).get_weekly_schedule(current_user.id, reference_date=ref_date)
    return [_to_response(i) for i in items]


@router.get("/by-date", response_model=list[ScheduleResponse])
def get_schedule_by_date(
    target_date: date = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ScheduleResponse]:
    items = ScheduleService(db).get_schedule_by_date(current_user.id, target_date)
    return [_to_response(i) for i in items]


@router.get("/conflicts", response_model=list[ScheduleConflict])
def get_schedule_conflicts(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ScheduleConflict]:
    start = start_date or local_today()
    end = end_date or (start + timedelta(days=6))
    return ScheduleService(db).detect_conflicts(current_user.id, start, end)


@router.get("/rooms", response_model=list[ScheduleResponse])
def search_rooms(
    q: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ScheduleResponse]:
    items = ScheduleService(db).find_rooms(current_user.id, q)
    return [_to_response(i) for i in items]


@router.post("", response_model=ScheduleResponse, status_code=status.HTTP_201_CREATED)
def upsert_schedule(
    payload: ScheduleUpsert,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScheduleResponse:
    try:
        item = ScheduleService(db).upsert_schedule(current_user.id, payload)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_response(item)
