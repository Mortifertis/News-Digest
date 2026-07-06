from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class NewsSource(Base):
    __tablename__ = "news_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    language: Mapped[str] = mapped_column(String(12))
    country: Mapped[str] = mapped_column(String(80))
    homepage_url: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    feeds: Mapped[list["FeedSubscription"]] = relationship(
        back_populates="source"
    )
    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class FeedSubscription(Base):
    __tablename__ = "feed_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("news_sources.id"))
    title: Mapped[str] = mapped_column(String(160))
    feed_url: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(12))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetch_status: Mapped[str] = mapped_column(String(20), default="never")
    last_fetch_error: Mapped[str | None] = mapped_column(Text)
    last_http_status: Mapped[int | None] = mapped_column()
    last_entries_count: Mapped[int] = mapped_column(default=0)
    last_new_articles_count: Mapped[int] = mapped_column(default=0)
    last_skipped_articles_count: Mapped[int] = mapped_column(default=0)
    last_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    source: Mapped[NewsSource] = relationship(back_populates="feeds")
    articles: Mapped[list["Article"]] = relationship(back_populates="feed")


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("feed_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("news_sources.id"))
    feed_id: Mapped[int] = mapped_column(ForeignKey("feed_subscriptions.id"))
    external_id: Mapped[str | None] = mapped_column(String(700), index=True)
    title: Mapped[str] = mapped_column(String(700))
    summary: Mapped[str] = mapped_column(Text, default="")
    canonical_url: Mapped[str] = mapped_column(String(700), index=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    language: Mapped[str] = mapped_column(String(12))
    normalized_title: Mapped[str] = mapped_column(String(700), index=True)
    normalized_summary: Mapped[str] = mapped_column(Text, default="")
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    source: Mapped[NewsSource] = relationship(back_populates="articles")
    feed: Mapped[FeedSubscription] = relationship(back_populates="articles")


class StoryCluster(Base):
    __tablename__ = "story_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(700))
    lead_article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"))
    language: Mapped[str] = mapped_column(String(12))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    lead_article: Mapped[Article] = relationship(
        foreign_keys=[lead_article_id]
    )
    articles: Mapped[list["ClusterArticle"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )


class ClusterArticle(Base):
    __tablename__ = "cluster_articles"
    __table_args__ = (UniqueConstraint("cluster_id", "article_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("story_clusters.id"))
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id"), unique=True
    )
    similarity_score: Mapped[float] = mapped_column(Float)
    match_type: Mapped[str] = mapped_column(String(40))

    cluster: Mapped[StoryCluster] = relationship(back_populates="articles")
    article: Mapped[Article] = relationship()
