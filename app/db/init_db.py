from sqlalchemy import inspect, text

from app.db.models import Base
from app.db.session import engine

FEED_STATUS_COLUMNS = {
    "last_fetch_status": "VARCHAR(20) DEFAULT 'never' NOT NULL",
    "last_fetch_error": "TEXT",
    "last_http_status": "INTEGER",
    "last_entries_count": "INTEGER DEFAULT 0 NOT NULL",
    "last_new_articles_count": "INTEGER DEFAULT 0 NOT NULL",
    "last_skipped_articles_count": "INTEGER DEFAULT 0 NOT NULL",
    "last_fetched_at": "DATETIME",
    "last_successful_fetch_at": "DATETIME",
}


def add_missing_poc_columns() -> None:
    inspector = inspect(engine)
    if "feed_subscriptions" not in inspector.get_table_names():
        return
    existing = {
        column["name"]
        for column in inspector.get_columns("feed_subscriptions")
    }
    with engine.begin() as connection:
        for name, ddl in FEED_STATUS_COLUMNS.items():
            if name not in existing:
                sql = f"ALTER TABLE feed_subscriptions ADD COLUMN {name} {ddl}"
                connection.execute(text(sql))


def main() -> None:
    Base.metadata.create_all(bind=engine)
    add_missing_poc_columns()
    print("SQLite database tables created/updated.")


if __name__ == "__main__":
    main()
