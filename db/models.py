import os
import datetime
from sqlalchemy import create_engine, ForeignKey, Text, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship
from datetime import timezone

Base = declarative_base()

def get_utc_now():
    return datetime.datetime.now(timezone.utc)

class Client(Base):
    __tablename__ = "clients"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    inn: Mapped[str] = mapped_column(String, unique=True, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now)
    notes: Mapped[str] = mapped_column(Text, nullable=True)

    representatives: Mapped[list["Representative"]] = relationship("Representative", back_populates="client")


class Representative(Base):
    __tablename__ = "representatives"

    telegram_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=True)
    username: Mapped[str] = mapped_column(String, nullable=True)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=True)
    
    client: Mapped["Client"] = relationship("Client", back_populates="representatives")
    admin_topic: Mapped["AdminTopic"] = relationship("AdminTopic", back_populates="representative", uselist=False)
    messages: Mapped[list["MessageHistory"]] = relationship("MessageHistory", back_populates="representative")


class AdminTopic(Base):
    __tablename__ = "admin_topics"

    topic_id: Mapped[int] = mapped_column(Integer, primary_key=True) # message_thread_id
    telegram_id: Mapped[int] = mapped_column(ForeignKey("representatives.telegram_id"), unique=True)
    status: Mapped[str] = mapped_column(String, default="active") # active, waiting_human, closed
    
    representative: Mapped["Representative"] = relationship("Representative", back_populates="admin_topic")


class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class MessageHistory(Base):
    __tablename__ = "messages_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(ForeignKey("representatives.telegram_id"))
    role: Mapped[str] = mapped_column(String, nullable=False) # user, assistant, admin
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now)
    
    representative: Mapped["Representative"] = relationship("Representative", back_populates="messages")
