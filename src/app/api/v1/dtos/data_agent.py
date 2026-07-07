"""Request/response models for the Data Agent (DB connection + sandboxed files)."""

from typing import List, Optional

from pydantic import BaseModel, Field, SecretStr

from src.app.core.common.model.message import Message


class ConnectDbRequest(BaseModel):
    """Credentials to connect a read-only database for the current session.

    The password is a SecretStr and is held only in server memory (never persisted/logged).
    """

    host: str = Field(..., min_length=1)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: SecretStr = Field(...)
    driver: str = Field(default="postgresql", description="SQLAlchemy dialect (e.g. postgresql, mysql+pymysql)")
    sslmode: Optional[str] = Field(default=None, description="e.g. require, prefer, disable (postgres)")


class ConnectDbResponse(BaseModel):
    connected: bool
    dialect: str
    table_count: int


class GrantFolderRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Absolute path of the folder to mount read-only")


class GrantFolderResponse(BaseModel):
    granted: bool
    folder: str


class DataQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class DataStreamRequest(BaseModel):
    messages: List[Message] = Field(..., min_length=1, description="Full conversation history")


class DataQueryResponse(BaseModel):
    messages: List[Message]


class HistoryStep(BaseModel):
    """One recorded tool invocation within an assistant turn (the chat's activity trail)."""

    name: str
    input: Optional[str] = None
    output: Optional[str] = None


class HistoryMessage(BaseModel):
    """A persisted message plus, for assistant turns, its ordered tool-activity steps."""

    role: str
    content: str
    steps: List[HistoryStep] = Field(default_factory=list)


class ChatHistoryResponse(BaseModel):
    """A session's persisted conversation, with per-turn activity for restoring the chat."""

    messages: List[HistoryMessage] = Field(default_factory=list)


class SourceStatusResponse(BaseModel):
    db_connected: bool = False
    dialect: Optional[str] = None
    folder: Optional[str] = None


class DisconnectResponse(BaseModel):
    message: str
