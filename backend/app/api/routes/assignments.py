from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.database.session import get_db
from app.models.enums import SubmissionStatus
from app.models.user import User
from app.schemas.assignment import AssignmentResponse
from app.services.assignment_service import AssignmentService

router = APIRouter(prefix="/assignments", tags=["assignments"])


@router.get("", response_model=list[AssignmentResponse])
def list_assignments(
    subject_id: int | None = Query(default=None),
    submission_status: SubmissionStatus | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AssignmentResponse]:
    items = AssignmentService(db).list_assignments(
        current_user.id,
        subject_id=subject_id,
        status=submission_status,
    )
    return [AssignmentResponse.model_validate(i) for i in items]


@router.get("/upcoming", response_model=list[AssignmentResponse])
def get_upcoming_deadlines(
    days: int = Query(default=7, ge=1, le=90),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AssignmentResponse]:
    items = AssignmentService(db).get_upcoming_deadlines(current_user.id, days=days)
    return [AssignmentResponse.model_validate(i) for i in items]


@router.get("/overdue", response_model=list[AssignmentResponse])
def get_overdue_assignments(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AssignmentResponse]:
    items = AssignmentService(db).get_overdue_assignments(current_user.id)
    return [AssignmentResponse.model_validate(i) for i in items]


@router.get("/{assignment_id}", response_model=AssignmentResponse)
def get_assignment_detail(
    assignment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AssignmentResponse:
    item = AssignmentService(db).get_assignment_detail(current_user.id, assignment_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found.")
    return AssignmentResponse.model_validate(item)
