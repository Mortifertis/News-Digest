from sqlalchemy import inspect, text

from app.db.models import Base
from app.db.session import engine

SOURCE_METADATA_COLUMNS = {
    "display_name": "VARCHAR(160)",
    "language_primary": "VARCHAR(12)",
    "languages_available": "TEXT",
    "region": "VARCHAR(80)",
    "outlet_type": "VARCHAR(40)",
    "ownership_type": "VARCHAR(40)",
    "paywall_level": "VARCHAR(20)",
    "editorial_reliability_score": "INTEGER",
    "bias_profile": "VARCHAR(40)",
    "rating_confidence": "VARCHAR(20)",
    "rating_notes": "TEXT",
    "default_priority": "INTEGER",
    "tags": "TEXT",
}

FEED_STATUS_COLUMNS = {
    "tags": "TEXT",
    "is_official_url": "VARCHAR(20)",
    "url_confidence": "VARCHAR(20)",
    "enabled_by_default": "BOOLEAN DEFAULT 0 NOT NULL",
    "notes": "TEXT",
    "last_fetch_status": "VARCHAR(20) DEFAULT 'never' NOT NULL",
    "last_fetch_error": "TEXT",
    "last_http_status": "INTEGER",
    "last_entries_count": "INTEGER DEFAULT 0 NOT NULL",
    "last_new_articles_count": "INTEGER DEFAULT 0 NOT NULL",
    "last_skipped_articles_count": "INTEGER DEFAULT 0 NOT NULL",
    "last_fetched_at": "DATETIME",
    "last_successful_fetch_at": "DATETIME",
}


def _add_missing_columns(table_name: str, columns: dict[str, str]) -> None:
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    with engine.begin() as connection:
        for name, ddl in columns.items():
            if name not in existing:
                sql = f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"
                connection.execute(text(sql))


def add_missing_poc_columns() -> None:
    _add_missing_columns("news_sources", SOURCE_METADATA_COLUMNS)
    _add_missing_columns("feed_subscriptions", FEED_STATUS_COLUMNS)


def main() -> None:
    Base.metadata.create_all(bind=engine)
    add_missing_poc_columns()
    print("SQLite database tables created/updated.")


if __name__ == "__main__":
    main()
