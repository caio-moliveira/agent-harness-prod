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
    # Opt-in to transparent resumption (#77): when the turn stops at the step cap, the server
    # resumes it in-stream instead of asking the user to press "continuar". Defaults to False so
    # the manual button stays the product's default. The client opts IN but never chooses how many
    # times — that ceiling is the server's MAX_AUTO_CONTINUES, or a tampered client could loop.
    auto_continue: bool = Field(
        default=False,
        description="Let the server auto-resume a step-capped turn, up to MAX_AUTO_CONTINUES times",
    )


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
    # Ingestion/manifest summary for the granted folder (drives the "N docs · M pages · indexing" chip).
    doc_count: int = 0
    page_count: int = 0
    indexing: bool = False


class DisconnectResponse(BaseModel):
    message: str


class SessionFileItem(BaseModel):
    """One entry in the session's granted folder, for the composer's `@` mention picker."""

    path: str = Field(description="Workspace-relative path, e.g. /vendas.csv")
    is_dir: bool = False


class SessionFilesResponse(BaseModel):
    """The (shallow) listing of a session's granted folder. Empty when no folder is granted."""

    files: List[SessionFileItem] = Field(default_factory=list)
