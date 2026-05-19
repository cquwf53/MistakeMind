from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import KNOWLEDGE_TREE, SUBJECTS
from app.models import KnowledgePoint, Subject


def ensure_seed_data(db: Session) -> None:
    subjects: dict[str, Subject] = {}
    for code, name in SUBJECTS.items():
        subject = db.scalar(select(Subject).where(Subject.code == code))
        if subject is None:
            subject = Subject(code=code, name=name)
            db.add(subject)
            db.flush()
        subjects[code] = subject

    for code, points in KNOWLEDGE_TREE.items():
        subject = subjects[code]
        for level1, level2, level3, keywords in points:
            exists = db.scalar(
                select(KnowledgePoint).where(
                    KnowledgePoint.subject_id == subject.id,
                    KnowledgePoint.level1 == level1,
                    KnowledgePoint.level2 == level2,
                    KnowledgePoint.level3 == level3,
                )
            )
            if exists is None:
                db.add(
                    KnowledgePoint(
                        subject_id=subject.id,
                        level1=level1,
                        level2=level2,
                        level3=level3,
                        keywords=keywords,
                    )
                )
    db.commit()
