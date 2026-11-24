# Rate Limiter with FastAPI and MongoDB
 
A backend rate limiter built with **FastAPI** and **pymongo**, backed by a local **MongoDB** instance (no Atlas or cluster needed). It uses the **sliding window log** algorithm and returns standard `429 Too Many Requests` responses with `Retry-After` headers.
 
## Features
 
- Sliding window log algorithm (no burst at window boundaries, unlike fixed windows)
- Atomic check-and-record: one `findOneAndUpdate` on a single document, so concurrent requests cannot exceed the limit
- Uses MongoDB server time (`$$NOW`), so clock differences between app servers do not matter
- TTL index automatically deletes idle clients
- Per-client limits, identified by the `X-API-Key` header, falling back to the client IP
- Standard headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `Retry-After`
- Fails open: if MongoDB is unreachable, requests are allowed instead of the API going down
## How it works
 
Each client has **one document** in the `limits` collection of the `ratelimiter` database:
 
```json
{
  "_id": "client-a",
  "hits": [1732350000123, 1732350004567],
  "allowed": true,
  "now": 1732350004567,
  "expireAt": "2024-11-23T08:21:04.567Z"
}
```
 
| Field | Meaning |
|---|---|
| `_id` | Client identifier (`X-API-Key` value or IP address) |
| `hits` | Timestamps (ms) of accepted requests still inside the window |
| `allowed` | Result of the most recent check |
| `now` | MongoDB server time (ms) of the most recent check, used to compute `Retry-After` |
| `expireAt` | Time after which the TTL index deletes the document |
 
For every request, the middleware runs a single `find_one_and_update` with an aggregation-pipeline update (`upsert=True`) that:
 
1. Removes hits older than the window
2. Checks whether the number of remaining hits is below the limit
3. Appends the current timestamp only if the request is allowed
4. Refreshes `expireAt` and records the server time in `now`
If the request is blocked, `Retry-After` is the time until the oldest hit leaves the window. A TTL index (`expireAfterSeconds=0` on `expireAt`) removes clients that have been idle for a full window.
 
## Requirements
 
- Python 3.9+
- MongoDB **4.2 or later** (needs aggregation-pipeline updates and `$$NOW`)
- MongoDB Shell (`mongosh`), optional, for inspecting data
## Project structure
 
```
.
├── main.py                # FastAPI app and rate limiter
├── test_limiter.py        # Demo / test script
├── mongosh_commands.js    # Handy commands for the MongoDB shell
├── requirements.txt       # Python dependencies
├── docker-compose.yml     # Optional: MongoDB in Docker
└── README.md
```
 
## Setup
 
### 1. Start a local MongoDB
 
**Windows (installed as a service):** MongoDB starts automatically. Check with:
 
```powershell
Get-Service MongoDB
```
 
If it is stopped, run `net start MongoDB` in an administrator PowerShell.
 
**macOS / Linux (manual):**
 
```bash
mkdir -p ~/mongo-data
mongod --dbpath ~/mongo-data
```
 
**Docker (optional):**
 
```bash
docker compose up -d
```
 
### 2. Install dependencies
 
```powershell
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```
 
If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process Bypass` first.
 
### 3. Run the API
 
```powershell
uvicorn main:app --reload
```
 
The server runs at `http://localhost:8000`. Interactive docs are at `http://localhost:8000/docs`.
 
## Configuration
 
Set these as environment variables before starting the server:
 
| Variable | Default | Description |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB` | `ratelimiter` | Database name |
| `RATE_LIMIT` | `5` | Max requests allowed per window |
| `RATE_WINDOW_SECONDS` | `60` | Window length in seconds |
 
Example (PowerShell), 10 requests per 30 seconds:
 
```powershell
$env:RATE_LIMIT = "10"
$env:RATE_WINDOW_SECONDS = "30"
uvicorn main:app --reload
```
 
## API endpoints
 
| Method | Path | Rate limited | Description |
|---|---|---|---|
| GET | `/` | Yes | Sample protected endpoint |
| GET | `/health` | No | Health check |
 
`/docs` and `/openapi.json` are also excluded from rate limiting.
 
### Example responses
 
**Allowed (200):**
 
```
HTTP/1.1 200 OK
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 3
 
{"message": "Request accepted"}
```
 
**Blocked (429):**
 
```
HTTP/1.1 429 Too Many Requests
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 0
Retry-After: 42
 
{"error": "Too Many Requests", "retry_after_seconds": 42}
```
 
## Testing
 
With the server running, open a second terminal and run:
 
```powershell
venv\Scripts\activate
python test_limiter.py
```
 
Expected output (default limit of 5):
 
```
Client A sends 8 requests (limit is 5 by default):
  #1: 200 remaining=4 retry-after=None
  #2: 200 remaining=3 retry-after=None
  #3: 200 remaining=2 retry-after=None
  #4: 200 remaining=1 retry-after=None
  #5: 200 remaining=0 retry-after=None
  #6: 429 remaining=0 retry-after=<seconds, at most 60>
  #7: 429 remaining=0 retry-after=<seconds, at most 60>
  #8: 429 remaining=0 retry-after=<seconds, at most 60>
Client B is unaffected by A:
   200
```
 
You can also test manually with curl:
 
```bash
curl -i -H "X-API-Key: my-client" http://localhost:8000/
```
 
Because the limiter keeps state in MongoDB, re-running the test within the same window will block client A immediately. Reset it first (see below).
 
## Inspecting data in the MongoDB shell
 
```bash
mongosh
```
 
```javascript
use ratelimiter
 
db.limits.find().pretty()                  // all clients and their hits
db.limits.findOne({ _id: "client-a" })     // one client
db.limits.getIndexes()                     // confirm the TTL index on expireAt
db.limits.deleteMany({})                   // reset the limiter
```
 
More commands, including running the rate-limit logic by hand in the shell, are in `mongosh_commands.js`.
 
## Troubleshooting
 
| Problem | Fix |
|---|---|
| `ConnectionRefusedError ... port 8000` when running the test | The API server is not running. Start it with `uvicorn main:app --reload` in another terminal. |
| `failed to connect to the docker API` | Docker Desktop is not running. Start it, or install MongoDB locally instead. |
| Every request is allowed even past the limit | MongoDB is unreachable and the limiter is failing open. Check that the MongoDB service is running. |
| Client stays blocked between test runs | Clear state with `db.limits.deleteMany({})` or wait for the window to pass. |
| `$$NOW` or pipeline update errors | MongoDB is older than 4.2. Upgrade to 4.2 or later. |
 
## Design notes and trade-offs
 
- **Accuracy vs. storage:** the sliding window log is exact but stores one timestamp per accepted request, so memory per client grows with the limit. A sliding window counter or token bucket would use less space at the cost of approximation.
- **Single-document atomicity:** keeping all of a client's hits in one document is what makes the update atomic without transactions or locks. Very large limits would push toward MongoDB's 16 MB document size limit.
- **Fail open vs. fail closed:** failing open keeps the API available during a database outage but temporarily disables protection. Change the `except PyMongoError` branch in `main.py` to return a 503 if you prefer fail closed.
- **Blocking driver:** pymongo is synchronous, so the check runs in a thread pool (`run_in_threadpool`) to avoid blocking the event loop.
## GitHub link
https://github.com/Abantikasen11/Rate-limiter
