# Internal API — Bot ↔ Backend Communication

This document covers the complete internal API system for bidirectional communication between the Discord bot and the backend via Railway Private Networking.

---

## Architecture

```
┌─────────────────────────────────────────────┐
│         Railway Project: Moddy              │
├─────────────────────────────────────────────┤
│                                             │
│  ┌──────────────────┐                      │
│  │     Backend      │                      │
│  │  (Python/FastAPI) │                      │
│  │  website-backend  │                      │
│  │ .railway.internal │                      │
│  │   Port: 8080      │                      │
│  └───────┬──────────┘                      │
│          │  ▲                               │
│          │  │  HTTP + JSON                  │
│          │  │  Authorization: Bearer SECRET │
│          │  │  (via Private Network)        │
│          ▼  │                               │
│  ┌──────────────────┐                      │
│  │   Bot Discord    │                      │
│  │   (Python)       │                      │
│  │     moddy        │                      │
│  │ .railway.internal │                      │
│  │                   │                      │
│  │  Port 8080 (Public)  ← Discord API     │
│  │  Port 3000 (Private) ← Internal comm   │
│  └──────────────────┘                      │
│                                             │
│  DNS: *.railway.internal (private only)    │
└─────────────────────────────────────────────┘
```

### Communication Flow

**Backend → Bot** (Port 3000):
- Notify bot of events (payments, upgrades)
- Update Discord roles
- Health checks

**Bot → Backend** (Port 8080):
- Retrieve user info from backend DB
- Notify backend of Discord events
- Health checks

### Key Properties
- HTTP + JSON only
- Railway Private Networking (`.railway.internal` URLs, never publicly exposed)
- Single shared secret (`INTERNAL_API_SECRET`)
- Global auth middleware (no per-endpoint auth)
- Bidirectional communication

---

## Environment Variables

### Bot-side (Railway service: `moddy`)

| Variable | Value | Description |
|---|---|---|
| `INTERNAL_API_SECRET` | `secrets.token_urlsafe(32)` | Shared secret (must be identical in backend) |
| `INTERNAL_PORT` | `3000` | Private HTTP server port |
| `BACKEND_INTERNAL_URL` | `http://website-backend.railway.internal:8080` | Backend internal URL |
| `MODDY_GUILD_ID` | `1394001780148535387` | Main Discord server ID |
| `MODDY_PREMIUM_ROLE_ID` | `1424149819185827954` | Premium role ID |

Generate the secret:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Bot Endpoints (Port 3000)

### GET `/internal/health`

Health check.

**Headers:** `Authorization: Bearer {SECRET}`

**Response (200):**
```json
{ "status": "healthy", "service": "discord-bot", "version": "1.0.0" }
```

### POST `/internal/notify`

Notify bot of a user event (payment, upgrade, cancellation).

**Headers:** `Authorization: Bearer {SECRET}`, `Content-Type: application/json`

**Payload:**
```json
{
  "discord_id": "123456789012345678",
  "action": "subscription_created",
  "plan": "moddy_max",
  "metadata": {
    "customer_id": "cus_ABC123",
    "email": "user@example.com",
    "subscription_type": "month"
  }
}
```

**Actions:** `subscription_created`, `subscription_cancelled`, `plan_upgraded`

**Response (200):**
```json
{ "success": true, "message": "User notified successfully", "notification_sent": true }
```

### POST `/internal/roles/update`

Update Discord roles for a user.

**Headers:** `Authorization: Bearer {SECRET}`, `Content-Type: application/json`

**Payload:**
```json
{
  "discord_id": "123456789012345678",
  "plan": "moddy_max",
  "add_roles": ["1424149819185827954"],
  "remove_roles": []
}
```

**Response (200):**
```json
{ "success": true, "message": "Roles updated successfully", "roles_updated": true, "guild_id": "1394001780148535387" }
```

---

## Backend Client (Bot → Backend)

The bot communicates with the backend via `BackendClient` in `services/backend_client.py`:

```python
from services import get_backend_client, BackendClientError

backend_client = get_backend_client()

# Health check
health = await backend_client.health_check()

# Get user info
user_info = await backend_client.get_user_info("123456789012345678")

# Notify backend of Discord event
await backend_client.notify_event(
    event_type="member_joined",
    discord_id="123456789012345678",
    metadata={"guild_id": "999888777666555444", "username": "JohnDoe"}
)
```

---

## Use Case Examples

### Subscription Purchase Flow
1. User pays on website (Stripe)
2. Backend receives Stripe webhook
3. Backend calls `POST /internal/notify` (action: `subscription_created`)
4. Backend calls `POST /internal/roles/update` (add premium role)
5. Bot sends DM to user + assigns role on main server

### Subscription Cancellation
1. Backend calls `/internal/notify` (action: `subscription_cancelled`)
2. Backend calls `/internal/roles/update` (remove premium role)

### Bot → Backend: User Info Lookup
```python
user_info = await backend_client.get_user_info(str(interaction.user.id))
if user_info["user_found"]:
    # Show subscription info
```

---

## Code Structure

```
internal_api/
├── server.py              # FastAPI app + /health endpoint
├── middleware/
│   └── auth.py            # Auth middleware (Bearer token)
└── routes/
    └── internal.py        # /internal/* route handlers

services/
├── backend_client.py      # HTTP client for backend communication
└── railway_diagnostic.py  # Railway connectivity diagnostics

schemas/
└── internal.py            # Pydantic schemas for API payloads
```

The internal HTTP server starts automatically in `main.py` via `asyncio.gather()` alongside the Discord bot. It listens on `0.0.0.0:{INTERNAL_PORT}` (default 3000).

---

## Security

### Authentication Middleware
All `/internal/*` requests are protected by a global middleware that:
1. Checks for `Authorization` header
2. Validates `Bearer {SECRET}` format
3. Compares secret against `INTERNAL_API_SECRET`

Failures return 401 (missing) or 403 (invalid).

### Port Separation
- **Port 8080** (public): Railway-exposed, used for Discord API + public health check
- **Port 3000** (private): Railway Private Network only, internal API endpoints

---

## Testing with curl

```bash
# Health check
curl -X GET http://moddy.railway.internal:3000/internal/health \
  -H "Authorization: Bearer ${INTERNAL_API_SECRET}"

# Notification
curl -X POST http://moddy.railway.internal:3000/internal/notify \
  -H "Authorization: Bearer ${INTERNAL_API_SECRET}" \
  -H "Content-Type: application/json" \
  -d '{"discord_id": "123456789", "action": "subscription_created", "plan": "moddy_max"}'

# Role update
curl -X POST http://moddy.railway.internal:3000/internal/roles/update \
  -H "Authorization: Bearer ${INTERNAL_API_SECRET}" \
  -H "Content-Type: application/json" \
  -d '{"discord_id": "123456789", "plan": "moddy_max", "add_roles": ["1424149819185827954"], "remove_roles": []}'
```

---

## Troubleshooting

| Problem | Check |
|---|---|
| Bot doesn't receive requests | Verify `INTERNAL_API_SECRET` matches in both services |
| 401 Unauthorized | Verify `Authorization: Bearer {SECRET}` header format |
| Connection refused | Check server is listening on port 3000, Railway Private Networking is enabled |
| Server doesn't start | Check for port conflicts, verify FastAPI/uvicorn in requirements.txt |

---

## Related Documentation

- [RAILWAY.md](RAILWAY.md) — Environment variables and deployment
- [endpoints/](endpoints/) — Individual endpoint specifications
- [BACKEND_INTEGRATION_STATUS.md](BACKEND_INTEGRATION_STATUS.md) — Integration diagnostic status
