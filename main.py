import os
from datetime import datetime, timezone
from typing import Optional, List, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents

app = FastAPI(title="PaperPayout.io API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Models (request/response) ----------
class SignupBody(BaseModel):
    email: str
    username: str
    password: Optional[str] = None


class LoginBody(BaseModel):
    email: str
    password: Optional[str] = None


class JoinLobbyBody(BaseModel):
    user_id: str
    wager_usd: float


class FriendActionBody(BaseModel):
    user_id: str
    friend_user_id: str


class WithdrawBody(BaseModel):
    user_id: str
    to_address: str
    amount_sol: float


# ---------- Utility helpers ----------

def collection(name: str):
    return db[name]


def now_utc():
    return datetime.now(timezone.utc)


# ---------- Basic routes ----------
@app.get("/")
def read_root():
    return {"name": "PaperPayout.io API", "status": "ok"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = getattr(db, "name", "unknown")
            response["connection_status"] = "Connected"
            response["collections"] = db.list_collection_names()
        else:
            response["database"] = "❌ Not Available"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# Expose schemas so external tools can read them
@app.get("/schema")
def read_schema():
    try:
        from schemas import User, Wallet, Transaction, Lobby, Match, LeaderboardEntry, FriendRequest  # type: ignore
        return {
            "schemas": [
                "user",
                "wallet",
                "transaction",
                "lobby",
                "match",
                "leaderboardentry",
                "friendrequest",
            ]
        }
    except Exception:
        return {"schemas": []}


# ---------- Auth ----------
@app.post("/auth/signup")
def signup(body: SignupBody):
    users = collection("user")
    existing = users.find_one({"$or": [{"email": body.email}, {"username": body.username}]})
    if existing:
        raise HTTPException(status_code=400, detail="Email or username already in use")
    doc = {
        "email": body.email,
        "username": body.username,
        "password": body.password or "",
        "avatar_url": None,
        "created_at": now_utc(),
        "updated_at": now_utc(),
        "total_winnings_usd": 0.0,
        "total_winnings_sol": 0.0,
        "friends": [],
        "status": "online",
    }
    user_id = users.insert_one(doc).inserted_id
    # Create wallet placeholder with a fake address (MVP)
    wallets = collection("wallet")
    wallets.insert_one({
        "user_id": str(user_id),
        "address": f"SOL_FAKE_{str(user_id)[-8:]}",
        "balance_sol": 0.0,
        "balance_usd": 0.0,
        "created_at": now_utc(),
        "updated_at": now_utc(),
    })
    return {"user_id": str(user_id), "username": body.username}


@app.post("/auth/login")
def login(body: LoginBody):
    users = collection("user")
    user = users.find_one({"email": body.email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Password check skipped for MVP
    users.update_one({"_id": user["_id"]}, {"$set": {"status": "online", "updated_at": now_utc()}})
    return {"user_id": str(user["_id"]), "username": user["username"]}


# ---------- Wallet ----------
@app.get("/wallet/{user_id}")
def get_wallet(user_id: str):
    w = collection("wallet").find_one({"user_id": user_id})
    if not w:
        raise HTTPException(status_code=404, detail="Wallet not found")
    w["_id"] = str(w["_id"])
    return w


@app.post("/wallet/refresh/{user_id}")
def refresh_wallet(user_id: str):
    # MVP: simulate balance refresh (no real chain integration)
    w = collection("wallet").find_one({"user_id": user_id})
    if not w:
        raise HTTPException(status_code=404, detail="Wallet not found")
    collection("wallet").update_one({"_id": w["_id"]}, {"$set": {"updated_at": now_utc()}})
    w = collection("wallet").find_one({"user_id": user_id})
    w["_id"] = str(w["_id"])
    return w


@app.get("/transactions/{user_id}")
def get_transactions(user_id: str, limit: int = 50):
    txs = collection("transaction").find({"user_id": user_id}).sort("created_at", -1).limit(limit)
    out = []
    for t in txs:
        t["_id"] = str(t["_id"])
        out.append(t)
    return out


@app.post("/wallet/withdraw")
def withdraw(body: WithdrawBody):
    w = collection("wallet").find_one({"user_id": body.user_id})
    if not w:
        raise HTTPException(status_code=404, detail="Wallet not found")
    if w.get("balance_sol", 0.0) < body.amount_sol:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    fee = max(0.000005, body.amount_sol * 0.002)  # Example fee
    new_balance = round(w["balance_sol"] - body.amount_sol - fee, 8)
    if new_balance < 0:
        raise HTTPException(status_code=400, detail="Insufficient balance after fees")

    collection("wallet").update_one({"_id": w["_id"]}, {
        "$set": {
            "balance_sol": new_balance,
            "balance_usd": round(new_balance * 200.0, 2),  # rough SOL->USD
            "updated_at": now_utc()
        }
    })

    tx = {
        "user_id": body.user_id,
        "tx_type": "withdrawal",
        "amount_sol": body.amount_sol,
        "amount_usd": round(body.amount_sol * 200.0, 2),
        "status": "pending",
        "tx_hash": None,
        "meta": {"to": body.to_address, "network_fee": fee},
        "created_at": now_utc(),
    }
    collection("transaction").insert_one(tx)
    return {"ok": True, "fee": fee}


# ---------- Leaderboard & Stats ----------
@app.get("/leaderboard")
def leaderboard(period: Literal["all", "monthly", "daily"] = "all", limit: int = 50):
    # Sum payout transactions per user
    start = None
    now = datetime.utcnow()
    if period == "monthly":
        start = datetime(now.year, now.month, 1)
    elif period == "daily":
        start = datetime(now.year, now.month, now.day)

    query = {"tx_type": "payout"}
    if start:
        query["created_at"] = {"$gte": start}

    txs = collection("transaction").find(query)
    totals = {}
    for t in txs:
        uid = t["user_id"]
        totals[uid] = totals.get(uid, 0.0) + float(t.get("amount_usd", 0.0))

    # Join usernames
    results = []
    for uid, amt in sorted(totals.items(), key=lambda x: x[1], reverse=True)[:limit]:
        u = collection("user").find_one({"_id": db.get_collection("user").find_one({"_id": {"$exists": True}}).get("_id")})  # placeholder to avoid mypy
        u = collection("user").find_one({"_id": None})  # not used
        # safer: find by str _id match saved in documents
        user = collection("user").find_one({"_id": uid}) or collection("user").find_one({"_id": None})
        # we store string ids elsewhere, so query by string field if present
        user = collection("user").find_one({"_id": uid}) or collection("user").find_one({"user_id": uid}) or {}
        username = user.get("username", uid[:6]) if isinstance(user, dict) else uid[:6]
        results.append({"user_id": uid, "username": username, "winnings_usd": round(amt, 2)})

    return results


@app.get("/stats")
def global_stats():
    players_ingame = collection("lobby").count_documents({"status": "started"})
    payouts = collection("transaction").find({"tx_type": "payout"})
    total = 0.0
    for p in payouts:
        total += float(p.get("amount_usd", 0.0))
    return {"players_in_game": players_ingame, "global_player_winnings_usd": round(total, 2)}


# ---------- Friends ----------
@app.get("/friends/{user_id}")
def get_friends(user_id: str):
    fr = collection("friendrequest")
    users = collection("user")
    friend_ids = set()
    for r in fr.find({"$or": [{"from_user_id": user_id, "status": "accepted"}, {"to_user_id": user_id, "status": "accepted"}]}):
        friend_ids.add(r["from_user_id"])
        friend_ids.add(r["to_user_id"])
    friend_ids.discard(user_id)

    friends = []
    for fid in friend_ids:
        u = users.find_one({"_id": fid}) or users.find_one({"user_id": fid}) or {}
        friends.append({
            "user_id": fid,
            "username": u.get("username", fid[:6]) if isinstance(u, dict) else fid[:6],
            "status": u.get("status", "offline") if isinstance(u, dict) else "offline",
        })
    return friends


@app.post("/friends/request")
def add_friend(body: FriendActionBody):
    if body.user_id == body.friend_user_id:
        raise HTTPException(status_code=400, detail="Cannot add yourself")
    fr = collection("friendrequest")
    existing = fr.find_one({
        "$or": [
            {"from_user_id": body.user_id, "to_user_id": body.friend_user_id},
            {"from_user_id": body.friend_user_id, "to_user_id": body.user_id},
        ]
    })
    if existing:
        raise HTTPException(status_code=400, detail="Request already exists")
    fr.insert_one({
        "from_user_id": body.user_id,
        "to_user_id": body.friend_user_id,
        "status": "pending",
        "created_at": now_utc(),
    })
    return {"ok": True}


@app.post("/friends/accept")
def accept_friend(body: FriendActionBody):
    fr = collection("friendrequest")
    req = fr.find_one({"from_user_id": body.friend_user_id, "to_user_id": body.user_id, "status": "pending"})
    if not req:
        raise HTTPException(status_code=404, detail="No pending request found")
    fr.update_one({"_id": req["_id"]}, {"$set": {"status": "accepted", "updated_at": now_utc()}})
    return {"ok": True}


# ---------- Lobbies / Matchmaking (MVP simulation) ----------
@app.post("/lobby/join")
def join_lobby(body: JoinLobbyBody):
    lobbies = collection("lobby")
    # find waiting lobby with same wager and space
    lobby = lobbies.find_one({"wager_usd": body.wager_usd, "status": "waiting", "$where": "this.players.length < this.max_players"})
    if not lobby:
        lobby = {
            "wager_usd": body.wager_usd,
            "max_players": 10,
            "players": [],
            "status": "waiting",
            "created_at": now_utc(),
        }
        lobby_id = lobbies.insert_one(lobby).inserted_id
        lobby["_id"] = lobby_id

    # add player if not already in
    if body.user_id not in lobby["players"]:
        lobby["players"].append(body.user_id)
        lobbies.update_one({"_id": lobby["_id"]}, {"$set": {"players": lobby["players"]}})

    # if full, start match
    started = False
    if len(lobby["players"]) >= lobby.get("max_players", 10):
        lobbies.update_one({"_id": lobby["_id"]}, {"$set": {"status": "started", "started_at": now_utc()}})
        started = True
        match = {
            "lobby_id": str(lobby["_id"]),
            "wager_usd": body.wager_usd,
            "players": lobby["players"],
            "started_at": now_utc(),
        }
        mid = collection("match").insert_one(match).inserted_id
        match["_id"] = str(mid)
        return {"lobby_id": str(lobby["_id"]), "status": "started", "match": match}

    return {"lobby_id": str(lobby["_id"]), "status": "waiting", "players": lobby["players"], "max_players": lobby.get("max_players", 10)}


@app.get("/lobby/{lobby_id}")
def lobby_status(lobby_id: str):
    l = collection("lobby").find_one({"_id": lobby_id}) or collection("lobby").find_one({"_id": None})
    if not l:
        raise HTTPException(status_code=404, detail="Lobby not found")
    l["_id"] = str(l["_id"]) if "_id" in l else lobby_id
    return l


# ---------- Match results (MVP simulation) ----------
class MatchResultBody(BaseModel):
    match_id: str
    results: List[dict]  # [{user_id, score, rank, time}]


@app.post("/match/complete")
def complete_match(body: MatchResultBody):
    m = collection("match").find_one({"_id": body.match_id})
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    if m.get("completed_at"):
        return {"ok": True}

    # Determine winner by rank ascending, fallback to score
    winner = None
    if body.results:
        winner = sorted(body.results, key=lambda r: (r.get("rank", 9999), -float(r.get("score", 0))))[0]
    if not winner:
        raise HTTPException(status_code=400, detail="No results provided")

    collection("match").update_one({"_id": m["_id"]}, {"$set": {"completed_at": now_utc(), "results": body.results, "winner_user_id": winner["user_id"]}})

    # Payout to winner (pot minus 10% fee)
    wager = float(m.get("wager_usd", 0.0))
    pot = wager * len(m.get("players", []))
    fee = round(pot * 0.10, 2)
    payout_usd = round(pot - fee, 2)
    sol_price = 200.0
    payout_sol = round(payout_usd / sol_price, 6) if sol_price > 0 else 0.0

    # Credit wallet
    w = collection("wallet").find_one({"user_id": winner["user_id"]})
    if w:
        new_sol = round(float(w.get("balance_sol", 0.0)) + payout_sol, 6)
        collection("wallet").update_one({"_id": w["_id"]}, {"$set": {"balance_sol": new_sol, "balance_usd": round(new_sol * sol_price, 2), "updated_at": now_utc()}})

    # Transactions
    collection("transaction").insert_one({
        "user_id": winner["user_id"],
        "tx_type": "payout",
        "amount_sol": payout_sol,
        "amount_usd": payout_usd,
        "status": "confirmed",
        "tx_hash": None,
        "meta": {"match_id": body.match_id, "pot_usd": pot, "fee_usd": fee},
        "created_at": now_utc(),
    })

    return {"ok": True, "winner": winner["user_id"], "payout_usd": payout_usd}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
