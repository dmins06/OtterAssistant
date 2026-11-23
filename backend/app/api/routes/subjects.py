from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.schemas.subject import SubjectResponse, SubjectUpsert
from app.services.subject_service import SubjectService

router = APIRouter(prefix="/subjects", tags=["subjects"])


@router.get("", response_model=list[SubjectResponse])
def list_subjects(
    q: str | None = Query(default=None, description="Search by subject name or code"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SubjectResponse]:
    service = SubjectService(db)
    subjects = (
        service.search_subjects(current_user.id, q)
        if q
        else service.list_subjects(current_user.id)
    )
    return [SubjectResponse.model_validate(s) for s in subjects]


@router.get("/{subject_id}", response_model=SubjectResponse)
def get_subject(
    subject_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubjectResponse:
    subject = SubjectService(db).get_subject(current_user.id, subject_id)
    if subject is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found.")
    return SubjectResponse.model_validate(subject)


@router.post("", response_model=SubjectResponse, status_code=status.HTTP_201_CREATED)
def upsert_subject(
    payload: SubjectUpsert,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubjectResponse:
    subject = SubjectService(db).upsert_subject(current_user.id, payload)
    return SubjectResponse.model_validate(subject)
