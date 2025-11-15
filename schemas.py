"""
Database Schemas for PaperPayout.io

Each Pydantic model represents a MongoDB collection.
Collection name is the lowercase of the class name (e.g., User -> "user").
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime


class User(BaseModel):
    email: str = Field(..., description="Unique email for login")
    username: str = Field(..., description="Public username")
    password: Optional[str] = Field(None, description="Password hash or placeholder (MVP)")
    avatar_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    total_winnings_usd: float = 0.0
    total_winnings_sol: float = 0.0
    friends: List[str] = []  # list of user ids (stringified)
    status: Literal["offline", "online", "ingame"] = "offline"


class Wallet(BaseModel):
    user_id: str
    address: str
    balance_sol: float = 0.0
    balance_usd: float = 0.0


class Transaction(BaseModel):
    user_id: str
    tx_type: Literal["deposit", "withdrawal", "wager", "payout", "fee"]
    amount_sol: float = 0.0
    amount_usd: float = 0.0
    status: Literal["pending", "confirmed", "failed"] = "pending"
    tx_hash: Optional[str] = None
    meta: Optional[dict] = None


class Lobby(BaseModel):
    wager_usd: float
    max_players: int = 10
    players: List[str] = []  # user ids
    status: Literal["waiting", "started", "completed"] = "waiting"
    match_id: Optional[str] = None


class Match(BaseModel):
    lobby_id: str
    wager_usd: float
    players: List[str]
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    winner_user_id: Optional[str] = None
    results: Optional[list] = None  # list of {user_id, score, rank, time}


class LeaderboardEntry(BaseModel):
    user_id: str
    username: str
    winnings_usd: float


class FriendRequest(BaseModel):
    from_user_id: str
    to_user_id: str
    status: Literal["pending", "accepted", "declined"] = "pending"
