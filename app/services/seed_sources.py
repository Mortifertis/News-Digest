from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.source_candidates import seed_all_candidate_sources


def seed(session: Session, *, default_enabled: bool = True) -> int:
    count = seed_all_candidate_sources(session)
    if default_enabled:
        # Keep manual source management: candidates stay disabled by default.
        session.commit()
    return count


def seed_all_candidates(session: Session) -> int:
    return seed_all_candidate_sources(session)


def main() -> None:
    with SessionLocal() as session:
        count = seed(session)
    print(f"Seeded source candidate catalog ({count} new feeds).")


if __name__ == "__main__":
    main()
