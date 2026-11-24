"""Rate limiter backend: FastAPI + MongoDB (pymongo), sliding window log.

Data model: ONE document per client in collection `limits`
    {
      _id:      "<client id>",           # X-API-Key header, else IP
      hits:     [1732350000123, ...],    # request timestamps (ms) inside the window
      allowed:  true/false,              # result of the latest check
      expireAt: ISODate(...)             # TTL index deletes idle clients
      now:      1732350000456            # DB server time (ms) of the latest check
    }

Atomicity: a single findOneAndUpdate with an aggregation-pipeline update
(MongoDB 4.2+) prunes old hits, checks the limit and appends the new hit in
ONE single-document operation, so concurrent requests cannot exceed the limit.
Time comes from the MongoDB server ($$NOW), so app-server clock skew is irrelevant.
"""
import os

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGO_DB", "ratelimiter")
LIMIT = int(os.getenv("RATE_LIMIT", "5"))                 # requests allowed
WINDOW_MS = int(os.getenv("RATE_WINDOW_SECONDS", "60")) * 1000

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
limits = client[DB_NAME]["limits"]

# TTL index: Mongo removes a client's document once expireAt has passed.
limits.create_index("expireAt", expireAfterSeconds=0)

NOW_MS = {"$toLong": "$$NOW"}


def check_rate_limit(client_key: str):
    """Returns (allowed, remaining, retry_after_ms)."""
    pipeline = [
        # 1. drop hits that fell out of the window
        {"$set": {"hits": {"$filter": {
            "input": {"$ifNull": ["$hits", []]},
            "as": "h",
            "cond": {"$gt": ["$$h", {"$subtract": [NOW_MS, WINDOW_MS]}]},
        }}}},
        # 2. is there room for one more?
        {"$set": {"allowed": {"$lt": [{"$size": "$hits"}, LIMIT]}}},
        # 3. record the hit only if allowed
        {"$set": {"hits": {"$cond": [
            "$allowed", {"$concatArrays": ["$hits", [NOW_MS]]}, "$hits"]}}},
        # 4. refresh TTL
        {"$set": {"expireAt": {"$add": ["$$NOW", WINDOW_MS]}, "now": NOW_MS}},
    ]
    doc = limits.find_one_and_update(
        {"_id": client_key}, pipeline,
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    hits = doc["hits"]
    if doc["allowed"]:
        return True, LIMIT - len(hits), 0
    # blocked: wait until the oldest hit leaves the window
    return False, 0, max(0, min(hits) + WINDOW_MS - doc["now"])


app = FastAPI(title="Rate Limiter Demo (MongoDB)")


def client_id(request: Request) -> str:
    return request.headers.get("x-api-key") or request.client.host


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path in ("/health", "/docs", "/openapi.json"):
        return await call_next(request)

    try:
        allowed, remaining, retry_ms = await run_in_threadpool(
            check_rate_limit, client_id(request))
    except PyMongoError:
        # Fail open: a database outage shouldn't take the API down.
        return await call_next(request)

    if not allowed:
        retry_s = max(1, -(-int(retry_ms) // 1000))   # ceil to seconds
        return JSONResponse(
            {"error": "Too Many Requests", "retry_after_seconds": retry_s},
            status_code=429,
            headers={"X-RateLimit-Limit": str(LIMIT),
                     "X-RateLimit-Remaining": "0",
                     "Retry-After": str(retry_s)},
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


@app.get("/")
async def index():
    return {"message": "Request accepted"}


@app.get("/health")
async def health():
    return {"status": "ok"}