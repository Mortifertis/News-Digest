from app.db.models import Base
from app.db.session import engine


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("SQLite database tables created.")


if __name__ == "__main__":
    main()
