import json, threading, sys, os, logging, time
from datetime import datetime, timedelta
from time import monotonic
from collections import defaultdict
from typing import Optional, Literal
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect, Request, Depends, Header, HTTPException, File, UploadFile, Form
from starlette.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn
import uuid  # to generate unique connection IDs
from Websocket.client_manager import ClientManager
from Websocket.__init__ import PewHits, PewHitsServer
from Websocket.models import SessionMetadata
from Websocket.webAPI import WebAPI, ClientInfo
from dataclasses import dataclass, asdict, is_dataclass
from pydantic import BaseModel

from config.client_actions_manager import ClientActionsManager

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from database.json_auth_service import json_auth_service
from config.config import Server, config
from config.settings_manager import SettingsManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

# Setup Jinja2 templates
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# Logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()

    # Log request
    logger.info(f"📥 {request.method} {request.url.path} from {request.client.host}")

    response = await call_next(request)

    # Log response
    duration = time.time() - start_time
    logger.info(f"📤 {request.method} {request.url.path} - Status: {response.status_code} - Duration: {duration:.3f}s")

    return response

# Allow cross-origin access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (change this for security)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models for requests
class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

@dataclass
class BotDefinition:
    radio: PewHits

file_path = "json/auth_clients.json"

def load_clients_info(file_path: str):
    # Load authorized clients
    with open(file_path, "r") as file:
        return json.load(file)

def get_client_info_by_key(api_key: str) -> Optional[ClientInfo]:
    """Authenticate client using client_auth_key"""
    AUTHORIZED_CLIENTS = load_clients_info(file_path)

    for client in AUTHORIZED_CLIENTS.values():
        if client.get("client_auth_key") == api_key:
            
            return ClientInfo(
                client_id=client.get("client_id"),
                # ⭐ CORRECTED: Passing the username
                username=client.get("username"), 
                client_name=client.get("client_name"),
                api_key=api_key,
                is_DJ=client.get("is_DJ", False),
                role=client.get("role", "user"),
                client_description=client.get("client_description")
            )

    return None

def get_client_info_by_jwt(token: str) -> Optional[ClientInfo]:
    """Authenticate client using JWT token"""
    payload = json_auth_service.verify_token(token)
    if not payload:
        return None

    # Note: Assumes get_user_by_id returns the user object with ALL fields
    user = json_auth_service.get_user_by_id(payload.get('user_id'))
    if not user:
        return None

    user_role = user.get('role', 'user')
    is_dj = user_role in ['dj', 'admin']

    return ClientInfo(
        client_id=user['client_id'],
        client_name=user['client_name'],
        api_key=token,
        is_DJ=is_dj,
        role=user_role, # ⭐ CORRECTED: Passing the role
        username=user.get('username'), # ⭐ ADDED: Passing the username
        client_description=f"{user_role.capitalize()} user"
    )

def generate_connection_id():
    return str(uuid.uuid4())

def is_api_key_valid(api_key: str, client_info: ClientInfo) -> bool:
    if client_info.api_key == api_key:
        return True
    return False

# JWT Authentication Dependency
async def get_current_user(authorization: Optional[str] = Header(None)):
    """Dependency to get current authenticated user from JWT"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authentication token")

    token = authorization.replace("Bearer ", "")
    payload = json_auth_service.verify_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = json_auth_service.get_user_by_id(payload.get('user_id'))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

rate_limiters = defaultdict(lambda: {"tokens": 10, "last_time": monotonic()})

RATE_LIMITS = {
    "/play": {"ip": (30, 60), "user": (20, 1200)},
    "/now-playing": (30, 120),  # 30 requests/2 minute
    "/next-coming": (30, 120),  # 30 requests/2 minute
    "/queue": (30, 120),  # 30 requests/2 minute
    "/remove": (30, 120),  # 30 requests/2 minute
    "/block": (30, 120),  # 30 requests/2 minute
    "/unblock": (30, 120),  # 30 requests/2 minute
    "/blocklist": (30, 120),
    "/skip": (1, 30),
    "/reloadall": (1, 30),
}

rate_limit_data = {
    "/play": {"ip": {}, "user": {}},
    "/now-playing": {},
    "/next-coming": {},
    "/queue": {},
    "/remove": {},
    "/block": {},
    "/unblock": {},
    "/blocklist": {},
    "/skip": {},
    "/reloadall": {},
}

def is_rate_limited(endpoint, identifier, is_user=False):
    limits = RATE_LIMITS.get(endpoint)
    if not limits:
        return False, 0

    limit_type = "user" if is_user else "ip"
    rate_limit = limits.get(limit_type)
    if not rate_limit:
        return False, 0

    max_requests, time_window = rate_limit
    now = datetime.now()  # Use datetime consistently

    # Get or initialize rate limit data
    endpoint_data = rate_limit_data[endpoint][limit_type]
    if identifier not in endpoint_data:
        endpoint_data[identifier] = []

    # Filter timestamps outside the window
    request_times = [
        t for t in endpoint_data[identifier]
        if t > now - timedelta(seconds=time_window)
    ]
    endpoint_data[identifier] = request_times

    # Check limit
    if len(request_times) >= max_requests:
        retry_after = (request_times[0] + timedelta(seconds=time_window) - now).total_seconds()
        return True, retry_after

    # Add current request
    endpoint_data[identifier].append(now)
    return False, 0

def rate_limit(ip_address, endpoint, username=None):
    now = datetime.now()

    if endpoint in rate_limit_data:
        if endpoint != "/play":
            max_requests, window = RATE_LIMITS[endpoint]
            if ip_address not in rate_limit_data[endpoint]:
                rate_limit_data[endpoint][ip_address] = []
            rate_limit_data[endpoint][ip_address] = [
                ts for ts in rate_limit_data[endpoint][ip_address]
                if ts > now - timedelta(seconds=window)
            ]
            if len(rate_limit_data[endpoint][ip_address]) >= max_requests:
                retry_after = (
                    rate_limit_data[endpoint][ip_address][0]
                    + timedelta(seconds=window)
                    - now
                ).total_seconds()
                return False, retry_after
            rate_limit_data[endpoint][ip_address].append(now)
        else:
            ip_max, ip_window = RATE_LIMITS["/play"]["ip"]
            user_max, user_window = RATE_LIMITS["/play"]["user"]

            if ip_address not in rate_limit_data["/play"]["ip"]:
                rate_limit_data["/play"]["ip"][ip_address] = []
            rate_limit_data["/play"]["ip"][ip_address] = [
                ts for ts in rate_limit_data["/play"]["ip"][ip_address]
                if ts > now - timedelta(seconds=ip_window)
            ]
            if len(rate_limit_data["/play"]["ip"][ip_address]) >= ip_max:
                retry_after = (
                    rate_limit_data["/play"]["ip"][ip_address][0]
                    + timedelta(seconds=ip_window)
                    - now
                ).total_seconds()
                return False, retry_after

            if username:
                if username not in rate_limit_data["/play"]["user"]:
                    rate_limit_data["/play"]["user"][username] = []
                rate_limit_data["/play"]["user"][username] = [
                    ts for ts in rate_limit_data["/play"]["user"][username]
                    if ts > now - timedelta(seconds=user_window)
                ]
                if len(rate_limit_data["/play"]["user"][username]) >= user_max:
                    retry_after = (
                        rate_limit_data["/play"]["user"][username][0]
                        + timedelta(seconds=user_window)
                        - now
                    ).total_seconds()
                    return False, retry_after

            rate_limit_data["/play"]["ip"][ip_address].append(now)
            if username:
                rate_limit_data["/play"]["user"][username].append(now)

    return True, None

async def send_rate_limited_response(websocket, endpoint, client_ip, username=None):
    if endpoint == "/play":
        # IP-based check
        allowed, retry_after = rate_limit(client_ip, endpoint)
        if not allowed:
            await websocket.send_json({
                "error": "Too many requests from your IP.",
                "code": 429,
                "retry_after": int(retry_after)
            })
            return True  # Blocked

        # Username-based check (if provided)
        if username:
            allowed, retry_after = is_rate_limited(endpoint, username, is_user=True)
            if allowed:  # Means it's rate-limited
                await websocket.send_json({
                    "error": "Requested too many songs within a short time, you can request again after a while.",
                    "code": 704,
                    "Retry-After": f"{int(retry_after)}"
                })
                return True
        return False

    # General endpoint check (non-/play)
    allowed, retry_after = rate_limit(client_ip, endpoint)
    if not allowed:
        await websocket.send_json({
            "error": "Too many requests",
            "retry_after": int(retry_after)
        })
        return True  # Blocked

    return False  # Allowed


# WebSocket Endpoint
# ============= WEB DASHBOARD & AUTH ROUTES =============

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the radio dashboard"""
    logger.info("📱 Serving dashboard HTML")
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/register")
async def register(data: RegisterRequest):
    """Register a new user"""
    logger.info(f"👤 Registration attempt for username: {data.username}")

    try:
        if json_auth_service.get_user_by_email(data.email):
            logger.warning(f"❌ Registration failed: Email {data.email} already registered")
            return JSONResponse({"error": "Email already registered"}, status_code=409)

        if json_auth_service.get_user_by_username(data.username):
            logger.warning(f"❌ Registration failed: Username {data.username} already taken")
            return JSONResponse({"error": "Username already taken"}, status_code=409)

        user = json_auth_service.create_user(data.username, data.email, data.password, "user")
        
        raw_token = json_auth_service.generate_verification_token(user['email'])
        
        VERIFY_URL = f"{Server.CLIENT_BASE_URL}/api/auth/verify-email?token={raw_token}"

        try:
            EmailSender.send_verification_email(user['email'], VERIFY_URL)
        except Exception as e:
            logger.error(f"❌ Failed to send verification email for {user['email']}: {e}")
        
        logger.info(f"✅ User registered, verification email sent to: {user['email']}")

        return {
            "success": True,
            "message": "Registration successful. Please check your email to verify your account."
        }
    
    except Exception as e:
        logger.error(f"❌ Registration error: {str(e)}")
        return JSONResponse({"error": f"Registration failed: {str(e)}"}, status_code=500)
    
@app.get("/api/auth/verify-email")
async def verify_email_endpoint(token: str = Query(..., description="Verification token")):
    """
    Handles the link click from the verification email.
    Validates the token, sets user.is_verified = True, and redirects the user.
    """
    
    try:
        success = json_auth_service.verify_and_delete_token(token)
        
        if success:
            logger.info(f"✅ Email verification successful for token: {token}")
            return RedirectResponse(
                url=f"{Server.CLIENT_BASE_URL}?status=email_verified", 
                status_code=302
            )
        else:
            logger.warning(f"❌ Email verification failed: Invalid or expired token: {token}")
            return RedirectResponse(
                url=f"{Server.CLIENT_BASE_URL}?status=verification_failed&error=invalid_token", 
                status_code=302
            )
            
    except Exception as e:
        logger.error(f"❌ Verification endpoint error for token {token}: {str(e)}")
        # Redirect for internal server errors
        return RedirectResponse(
            url=f"{Server.CLIENT_BASE_URL}?status=verification_failed&error=server_error", 
            status_code=302
        )

@app.post("/api/login")
async def login(data: LoginRequest):
    """Login user and check if their email address is verified."""
    logger.info(f"🔑 Login attempt for username: {data.username}")

    try:
        user = json_auth_service.get_user_by_username(data.username)

        # 1. Check Username and Password (Existing Logic)
        if not user or not json_auth_service.verify_password(data.password, user.get("password_hash", "")):
            logger.warning(f"❌ Login failed: Invalid credentials for {data.username}")
            return JSONResponse({"error": "Invalid username or password"}, status_code=401)

        # 2. 🔑 CHECK EMAIL VERIFICATION STATUS (NEW LOGIC)
        # We check for 'is_verified' and default to True if the key doesn't exist
        # (This handles old user accounts created before the feature was added)
        if not user.get("is_verified", True):
            logger.warning(f"❌ Login failed: Unverified account for {data.username}")
            # Use a specific error message for the frontend to handle
            return JSONResponse({"error": "Email unverified. Please check your inbox for the verification link."}, status_code=403)
        
        # 3. 🚫 CHECK BAN STATUS (NEW LOGIC)
        # We check against the username provided in the login data
        if ClientActionsManager.is_banned(data.username):
            logger.warning(f"❌ Login failed: Banned user attempt for {data.username}")
            # Use a clear error message and status code for a forbidden action
            return JSONResponse({"error": "Access denied. Your account has been banned."}, status_code=403)
        
        # 4. ✅ CHECK MUTE STATUS (DO NOT BLOCK LOGIN)
        is_user_muted = ClientActionsManager.is_muted(data.username)
        if is_user_muted:
            logger.warning(f"🔔 Muted user successfully logged in: {data.username}")
            # We log the warning but allow login to proceed

        # 5. Proceed with Login and Update Last Login
        json_auth_service.update_last_login(user['id'])

        access_token = json_auth_service.create_access_token({"user_id": user['id'], "role": user['role']})

        logger.info(f"✅ User logged in successfully: {user['client_name']} (Role: {user['role']})")

        return {
            "success": True,
            "user": {
                "id": user['id'],
                "email": user.get("email", ""),
                "username": user['client_name'],
                "role": user['role'],
                "is_muted": is_user_muted 
            },
            "access_token": access_token
        }
    except Exception as e:
        logger.error(f"❌ Login error: {str(e)}")
        return JSONResponse({"error": f"Login failed: {str(e)}"}, status_code=500)

@app.get("/api/me")
async def get_current_user_info(user = Depends(get_current_user)):
    """Get current authenticated user"""
    logger.info(f"👤 User info requested for: {user['client_name']}")

    return {
        "user": {
            "id": user['id'],
            "email": user.get("email", ""),
            "username": user['client_name'],
            "role": user['role'],
            "api_key": user.get("client_auth_key", "")
        }
    }
    
@app.get("/api/get-stream-url")
async def get_stream_url():
    """
    Returns the full streaming URL constructed from server configuration.
    """
    try:
        # Construct the URL using the configuration fields
        stream_url = (
            f"http://{config.SERVER_HOST}:{config.SERVER_PORT}"
            f"{config.MOUNT_POINT}"
        )
        
        # Return the URL in the required JSON format
        return {"url": stream_url}

    except Exception as e:
        logger.error(f"Error generating stream URL: {e}")
        # Fallback response or appropriate error handling
        return {"url": "http://80.225.211.98:8261/pewhits"}

@app.get("/api/search")
async def search_songs(q: str, limit: int = 10, user = Depends(get_current_user)):
    """Search for songs on Spotify"""
    # Check if search feature is enabled
    if not SettingsManager.is_enabled("api_search"):
        return JSONResponse({"error": "Search feature is currently disabled"}, status_code=403)
    
    logger.info(f"🔍 Song search for: {q} by user: {user['client_name']}")

    try:
        # Import Spotify here to avoid circular imports
        from config.DJ import downloader

        # Search Spotify
        results = downloader.sp.search(q=q, type='track', limit=limit)
        tracks = results['tracks']['items']

        # Format results
        songs = []
        for track in tracks:
            songs.append({
                "id": track['id'],
                "title": track['name'],
                "artist": ", ".join([artist['name'] for artist in track['artists']]),
                "album": track['album']['name'],
                "albumart": track['album']['images'][0]['url'] if track['album']['images'] else None,
                "duration": track['duration_ms'] // 1000,
                "spotify_url": track['external_urls']['spotify']
            })

        logger.info(f"✅ Found {len(songs)} results for: {q}")
        return {"results": songs}
    except Exception as e:
        logger.error(f"❌ Search error: {str(e)}")
        return JSONResponse({"error": f"Search failed: {str(e)}"}, status_code=500)

@app.get("/api/users")
async def get_all_users(user = Depends(get_current_user)):
    """Get all users (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)

    users = json_auth_service.get_all_users()
    return {
        "users": [{
            "id": u["client_id"],
            "email": u.get("email", ""),
            "username": u["client_name"],
            "role": u.get("role", "user"),
            "created_at": u.get("created_at", ""),
            "last_login": u.get("last_login", "")
        } for u in users]
    }

class UpdateUserRoleRequest(BaseModel):
    user_id: str
    new_role: str  # "user", "dj", or "admin"

@app.post("/api/users/role")
async def update_user_role(data: UpdateUserRoleRequest, user = Depends(get_current_user)):
    """Update user role (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)

    if data.new_role not in ["user", "dj", "admin"]:
        return JSONResponse({"error": "Invalid role"}, status_code=400)

    success = json_auth_service.update_user_role(data.user_id, data.new_role)
    if not success:
        return JSONResponse({"error": "User not found"}, status_code=404)

    logger.info(f"✅ Admin {user['client_name']} changed role to {data.new_role}")
    return {"success": True, "message": f"Role updated to {data.new_role}"}

class DeleteUserRequest(BaseModel):
    user_id: str

@app.post("/api/users/delete")
async def delete_user(data: DeleteUserRequest, user = Depends(get_current_user)):
    """Delete user account (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)

    if data.user_id == user['client_id']:
        return JSONResponse({"error": "Cannot delete your own account"}, status_code=400)

    success = json_auth_service.delete_user(data.user_id)
    if not success:
        return JSONResponse({"error": "User not found"}, status_code=404)

    logger.info(f"✅ Admin {user['client_name']} deleted user: {data.user_id}")
    return {"success": True, "message": "User deleted"}

class UpdatePasswordRequest(BaseModel):
    user_id: str
    new_password: str

@app.post("/api/users/password")
async def update_user_password(data: UpdatePasswordRequest, user = Depends(get_current_user)):
    """Update user password (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)

    target_user = json_auth_service.get_user_by_id(data.user_id)
    if not target_user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    # Update password
    json_auth_service.update_password(data.user_id, data.new_password)

    logger.info(f"✅ Admin {user['client_name']} updated password for user: {data.user_id}")
    return {"success": True, "message": "Password updated"}

class RegenerateAPIKeyRequest(BaseModel):
    user_id: str

@app.post("/api/users/regenerate-key")
async def regenerate_api_key(data: RegenerateAPIKeyRequest, user = Depends(get_current_user)):
    """Regenerate API key for user (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)

    new_api_key = json_auth_service.regenerate_api_key(data.user_id)
    if not new_api_key:
        return JSONResponse({"error": "User not found"}, status_code=404)

    logger.info(f"✅ Admin {user['client_name']} regenerated API key for user: {data.user_id}")
    return {"success": True, "api_key": new_api_key}

# ============= SELF-SERVICE PROFILE MANAGEMENT =============

class ChangeUsernameRequest(BaseModel):
    new_username: str

@app.post("/api/profile/change-username")
async def change_username(data: ChangeUsernameRequest, user = Depends(get_current_user)):
    """Change own username"""
    # Check if username is already taken
    existing_user = json_auth_service.get_user_by_username(data.new_username)
    if existing_user and existing_user['client_id'] != user['client_id']:
        return JSONResponse({"error": "Username already taken"}, status_code=409)
    
    success = json_auth_service.update_username(user['client_id'], data.new_username)
    if not success:
        return JSONResponse({"error": "Failed to update username"}, status_code=500)
    
    logger.info(f"✅ User {user['client_name']} changed username to {data.new_username}")
    return {"success": True, "message": "Username updated successfully"}

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@app.post("/api/profile/change-password")
async def change_own_password(data: ChangePasswordRequest, user = Depends(get_current_user)):
    """Change own password"""
    # Verify current password
    user_data = json_auth_service.get_user_by_id(user['client_id'])
    if not json_auth_service.verify_password(data.current_password, user_data.get("password_hash", "")):
        return JSONResponse({"error": "Current password is incorrect"}, status_code=401)
    
    json_auth_service.update_password(user['client_id'], data.new_password)
    logger.info(f"✅ User {user['client_name']} changed their password")
    return {"success": True, "message": "Password updated successfully"}

@app.post("/api/profile/regenerate-api-key")
async def regenerate_own_api_key(user = Depends(get_current_user)):
    """Regenerate own API key"""
    new_api_key = json_auth_service.regenerate_api_key(user['client_id'])
    if not new_api_key:
        return JSONResponse({"error": "Failed to regenerate API key"}, status_code=500)
    
    logger.info(f"✅ User {user['client_name']} regenerated their API key")
    return {"success": True, "api_key": new_api_key}

# ============= FORGOT PASSWORD WITH OTP =============

class ForgotPasswordRequest(BaseModel):
    email: str

class VerifyOTPRequest(BaseModel):
    email: str
    otp: str

class ResetPasswordRequest(BaseModel):
    email: str
    otp: str
    new_password: str

def save_otp(email: str, otp: str):
    """Save OTP with 5 minute expiry"""
    import json
    from datetime import datetime, timedelta
    
    try:
        with open("json/otp_storage.json", "r") as f:
            otp_data = json.load(f)
    except:
        otp_data = {}
    
    expiry = (datetime.now() + timedelta(minutes=5)).isoformat()
    otp_data[email] = {"otp": otp, "expiry": expiry}
    
    with open("json/otp_storage.json", "w") as f:
        json.dump(otp_data, f, indent=4)

def verify_otp(email: str, otp: str) -> bool:
    """Verify OTP and check expiry"""
    import json
    from datetime import datetime
    
    try:
        with open("json/otp_storage.json", "r") as f:
            otp_data = json.load(f)
    except:
        return False
    
    if email not in otp_data:
        return False
    
    stored_data = otp_data[email]
    expiry = datetime.fromisoformat(stored_data["expiry"])
    
    if datetime.now() > expiry:
        # Remove expired OTP
        del otp_data[email]
        with open("json/otp_storage.json", "w") as f:
            json.dump(otp_data, f, indent=4)
        return False
    
    return stored_data["otp"] == otp

def remove_otp(email: str):
    """Remove OTP after successful use"""
    import json
    
    try:
        with open("json/otp_storage.json", "r") as f:
            otp_data = json.load(f)
        
        if email in otp_data:
            del otp_data[email]
            with open("json/otp_storage.json", "w") as f:
                json.dump(otp_data, f, indent=4)
    except:
        pass

@app.post("/api/auth/forgot-password")
async def forgot_password(data: ForgotPasswordRequest):
    """Send OTP to email for password reset"""
    user = json_auth_service.get_user_by_email(data.email)
    if not user:
        # Don't reveal if email exists for security
        return {"success": True, "message": "If email exists, OTP has been sent"}
    
    # Generate 6-digit OTP
    import random
    otp = str(random.randint(100000, 999999))
    
    # Save OTP
    save_otp(data.email, otp)
    
    try:
        emailSender.send_otp_email(data.email, otp)
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return {"success": False, "message": "Failed to send OTP email"}
    
    return {"success": True, "message": "OTP sent to email (check server logs for demo)", "otp_demo": otp}

# ---------------- EMAIL HELPER ----------------
class EmailSender:
    
    @staticmethod # <--- ADD THIS DECORATOR
    def send_otp_email(receiver_email: str, otp: str):
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from config.config import config
        """Send a professional, branded OTP email."""
        subject = "🔐 Reset Your Pew Hits Account Password"

        # Professional HTML email template
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f6f9fc; padding: 40px;">
            <table style="max-width: 600px; margin: auto; background: #fff; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                <tr>
                    <td style="padding: 20px; text-align: center; background: #111827; border-top-left-radius: 10px; border-top-right-radius: 10px;">
                        <h1 style="color: #ffffff; margin: 0;">🎧 Pew Hits</h1>
                        <p style="color: #9ca3af; font-size: 14px;">Your Music. Your Vibe. Your World.</p>
                    </td>
                </tr>
                <tr>
                    <td style="padding: 30px;">
                        <h2 style="color: #111827;">Password Reset Request</h2>
                        <p style="color: #374151; font-size: 15px;">
                            We received a request to reset your Pew Hits account password. 
                            Use the following One-Time Password (OTP) to proceed with resetting your password:
                        </p>
                        <div style="text-align: center; margin: 30px 0;">
                            <p style="font-size: 32px; letter-spacing: 4px; font-weight: bold; color: #111827; background: #f3f4f6; display: inline-block; padding: 10px 20px; border-radius: 8px;">
                                {otp}
                            </p>
                        </div>
                        <p style="color: #6b7280; font-size: 14px;">
                            This OTP is valid for <strong>5 minutes</strong>. 
                            If you didn’t request this, please ignore this email — your account is safe.
                        </p>
                        <p style="margin-top: 40px; color: #9ca3af; font-size: 13px; text-align: center;">
                            © 2025 Pew Hits Radio. All rights reserved.<br>
                            Mumbai, India
                        </p>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

        # Create message
        msg = MIMEMultipart()
        msg["From"] = f"Pew Hits Radio <{config.SENDER_EMAIL}>"
        msg["To"] = receiver_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_content, "html"))

        # Send via Gmail SMTP
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(config.SENDER_EMAIL, config.APP_PASSWORD)
            server.send_message(msg)
            logger.info(f"📧 Sent OTP email to {receiver_email}")
            
    @staticmethod # <--- ADD THIS DECORATOR
    def send_verification_email(to_email: str, verify_url: str):
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from config.config import config
        
        subject = "✅ Verify Your Pew Hits Account Email"

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f9fafb; padding: 40px;">
            <table style="max-width: 600px; margin: auto; background: #fff; border-radius: 10px; box-shadow: 0 3px 8px rgba(0,0,0,0.05);">
                <tr>
                    <td style="background-color: #111827; color: white; text-align: center; padding: 20px; border-top-left-radius: 10px; border-top-right-radius: 10px;">
                        <h1 style="margin: 0;">🎧 Pew Hits</h1>
                        <p style="color: #9ca3af; font-size: 14px;">Your Music. Your Vibe. Your World.</p>
                    </td>
                </tr>
                <tr>
                    <td style="padding: 30px;">
                        <h2 style="color: #111827;">Verify Your Email Address</h2>
                        <p style="color: #374151;">
                            Thanks for creating a Pew Hits account! Please confirm that 
                            <strong>{to_email}</strong> is your email address by clicking the button below.
                        </p>
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="{verify_url}" 
                            style="background-color: #2563eb; color: white; padding: 12px 30px; 
                                    text-decoration: none; border-radius: 6px; font-weight: bold;">
                                Verify Email
                            </a>
                        </div>
                        <p style="color: #6b7280; font-size: 14px;">
                            This link will expire in 24 hours.<br>
                            If you didn’t create a Pew Hits account, you can safely ignore this message.
                        </p>
                        <hr style="border:none; border-top:1px solid #e5e7eb; margin-top:40px;">
                        <p style="margin-top: 40px; color: #9ca3af; font-size: 13px; text-align: center;">
                            © 2025 Pew Hits Radio. All rights reserved.<br>
                            Mumbai, India
                        </p>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

        msg = MIMEMultipart()
        msg["From"] = f"Pew Hits Radio <{config.SENDER_EMAIL}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(config.SENDER_EMAIL, config.APP_PASSWORD)
            server.send_message(msg)

        logger.info(f"📨 Sent verification email to {to_email}")

    @staticmethod
    def send_password_reset_confirmation_email(receiver_email: str):
        """Send an email confirming a successful password reset."""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from config.config import config
        
        subject = "✅ Your Pew Hits Password Has Been Successfully Reset"

        # Professional HTML email template
        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f6f9fc; padding: 40px;">
            <table style="max-width: 600px; margin: auto; background: #fff; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                <tr>
                    <td style="padding: 20px; text-align: center; background: #111827; border-top-left-radius: 10px; border-top-right-radius: 10px;">
                        <h1 style="color: #ffffff; margin: 0;">🎧 Pew Hits</h1>
                        <p style="color: #9ca3af; font-size: 14px;">Security Alert</p>
                    </td>
                </tr>
                <tr>
                    <td style="padding: 30px;">
                        <h2 style="color: #111827;">Password Successfully Updated</h2>
                        <p style="color: #374151; font-size: 15px;">
                            This is an automatic notification to confirm that the password for the account associated with <strong>{receiver_email}</strong> has been successfully changed.
                        </p>
                        <div style="background: #eef2ff; border-left: 5px solid #4f46e5; padding: 15px; margin: 20px 0;">
                            <p style="font-size: 16px; font-weight: bold; color: #111827; margin: 0;">
                                If you performed this action, you can safely ignore this email.
                            </p>
                        </div>
                        <p style="color: #374151; font-size: 15px;">
                            ⚠️ <strong>Security Warning:</strong> If you *did not* change your password, please contact our support team immediately as your account may have been compromised.
                        </p>
                        <p style="margin-top: 40px; color: #9ca3af; font-size: 13px; text-align: center;">
                            © 2025 Pew Hits Radio. All rights reserved.<br>
                            Mumbai, India
                        </p>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

        # Create message
        msg = MIMEMultipart()
        msg["From"] = f"Pew Hits Radio <{config.SENDER_EMAIL}>"
        msg["To"] = receiver_email
        msg["Subject"] = subject
        msg.attach(MIMEText(html_content, "html"))

        # Send via Gmail SMTP
        try:
            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(config.SENDER_EMAIL, config.APP_PASSWORD)
                server.send_message(msg)
                logger.info(f"📧 Sent password reset confirmation email to {receiver_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send confirmation email to {receiver_email}: {e}")
            return False
        
emailSender = EmailSender()

@app.post("/api/auth/verify-otp")
async def verify_otp_endpoint(data: VerifyOTPRequest):
    """Verify OTP code"""
    if verify_otp(data.email, data.otp):
        return {"success": True, "message": "OTP verified"}
    else:
        return JSONResponse({"error": "Invalid or expired OTP"}, status_code=401)

@app.post("/api/auth/reset-password")
async def reset_password(data: ResetPasswordRequest):
    """Reset password with OTP"""
    if not verify_otp(data.email, data.otp):
        return JSONResponse({"error": "Invalid or expired OTP"}, status_code=401)
    
    user = json_auth_service.get_user_by_email(data.email)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    # Update password
    json_auth_service.update_password(user['client_id'], data.new_password)
    
    # Remove used OTP
    remove_otp(data.email)
    
    logger.info(f"✅ Password reset successful for: {data.email}")
    # Send confirmation email
    emailSender.send_password_reset_confirmation_email(data.email)
    return {"success": True, "message": "Password reset successful"}

# ============= DJ ROLE REQUEST SYSTEM =============

@app.post("/api/role-requests")
async def submit_role_request(user = Depends(get_current_user)):
    """Request DJ role (user only)"""
    if user['role'] != "user":
        return JSONResponse({"error": "Only regular users can request DJ role"}, status_code=400)
    
    import json
    from datetime import datetime
    
    try:
        with open("json/role_requests.json", "r") as f:
            requests = json.load(f)
    except:
        requests = []
    
    # Check if user already has pending request
    for req in requests:
        if req['user_id'] == user['client_id'] and req['status'] == 'pending':
            return JSONResponse({"error": "You already have a pending request"}, status_code=409)
    
    # Add new request
    new_request = {
        "id": len(requests) + 1,
        "user_id": user['client_id'],
        "username": user['client_name'],
        "email": user.get('email', ''),
        "requested_at": datetime.now().isoformat(),
        "status": "pending"
    }
    requests.append(new_request)
    
    with open("json/role_requests.json", "w") as f:
        json.dump(requests, f, indent=4)
    
    logger.info(f"📩 DJ role request from: {user['client_name']}")
    return {"success": True, "message": "DJ role request submitted"}

@app.get("/api/role-requests")
async def get_role_requests(user = Depends(get_current_user)):
    """Get all role requests (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    import json
    try:
        with open("json/role_requests.json", "r") as f:
            requests = json.load(f)
        # Only return pending requests
        pending = [r for r in requests if r['status'] == 'pending']
        return {"requests": pending}
    except:
        return {"requests": []}

class RoleRequestActionRequest(BaseModel):
    request_id: int

@app.post("/api/role-requests/approve")
async def approve_role_request(data: RoleRequestActionRequest, user = Depends(get_current_user)):
    """Approve DJ role request (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    import json
    try:
        with open("json/role_requests.json", "r") as f:
            requests = json.load(f)
        
        for req in requests:
            if req['id'] == data.request_id and req['status'] == 'pending':
                # Update user role
                json_auth_service.update_user_role(req['user_id'], 'dj')
                
                # Mark request as approved
                req['status'] = 'approved'
                req['approved_by'] = user['client_name']
                req['approved_at'] = datetime.now().isoformat()
                
                with open("json/role_requests.json", "w") as f:
                    json.dump(requests, f, indent=4)
                
                logger.info(f"✅ Admin {user['client_name']} approved DJ role for: {req['username']}")
                return {"success": True, "message": f"{req['username']} is now a DJ"}
        
        return JSONResponse({"error": "Request not found"}, status_code=404)
    except Exception as e:
        logger.error(f"❌ Error approving role request: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/role-requests/deny")
async def deny_role_request(data: RoleRequestActionRequest, user = Depends(get_current_user)):
    """Deny DJ role request (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    import json
    from datetime import datetime
    
    try:
        with open("json/role_requests.json", "r") as f:
            requests = json.load(f)
        
        for req in requests:
            if req['id'] == data.request_id and req['status'] == 'pending':
                # Mark request as denied
                req['status'] = 'denied'
                req['denied_by'] = user['client_name']
                req['denied_at'] = datetime.now().isoformat()
                
                with open("json/role_requests.json", "w") as f:
                    json.dump(requests, f, indent=4)
                
                logger.info(f"❌ Admin {user['client_name']} denied DJ role for: {req['username']}")
                return {"success": True, "message": f"Request from {req['username']} denied"}
        
        return JSONResponse({"error": "Request not found"}, status_code=404)
    except Exception as e:
        logger.error(f"❌ Error denying role request: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ============= API KEY REQUEST SYSTEM =============

@app.post("/api/api-key-requests")
async def submit_api_key_request(user = Depends(get_current_user)):
    """Request API key access"""
    # Don't allow if user already has an API key
    if user.get('client_auth_key'):
        return JSONResponse({"error": "You already have an API key"}, status_code=400)
    
    import json
    from datetime import datetime
    
    try:
        with open("json/api_key_requests.json", "r") as f:
            requests = json.load(f)
    except:
        requests = []
    
    # Check if user already has pending request
    for req in requests:
        if req['user_id'] == user['id'] and req['status'] == 'pending':
            return JSONResponse({"error": "You already have a pending request"}, status_code=409)
    
    # Add new request
    new_request = {
        "id": len(requests) + 1,
        "user_id": user['id'],
        "username": user['client_name'],
        "email": user.get('email', ''),
        "status": "pending",
        "requested_at": datetime.now().isoformat()
    }
    
    requests.append(new_request)
    
    with open("json/api_key_requests.json", "w") as f:
        json.dump(requests, f, indent=4)
    
    logger.info(f"📩 API key request from: {user['client_name']}")
    return {"success": True, "message": "API key request submitted"}

@app.get("/api/api-key-requests")
async def get_api_key_requests(user = Depends(get_current_user)):
    """Get all pending API key requests (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    import json
    try:
        with open("json/api_key_requests.json", "r") as f:
            requests = json.load(f)
        
        # Return only pending requests
        pending_requests = [req for req in requests if req['status'] == 'pending']
        return {"requests": pending_requests}
    except:
        return {"requests": []}

class ApiKeyRequestActionRequest(BaseModel):
    request_id: int

@app.post("/api/api-key-requests/approve")
async def approve_api_key_request(data: ApiKeyRequestActionRequest, user = Depends(get_current_user)):
    """Approve API key request (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    import json
    from datetime import datetime
    
    try:
        with open("json/api_key_requests.json", "r") as f:
            requests = json.load(f)
        
        for req in requests:
            if req['id'] == data.request_id and req['status'] == 'pending':
                # Generate API key for user
                new_api_key = json_auth_service.regenerate_api_key(req['user_id'])
                
                if not new_api_key:
                    return JSONResponse({"error": "Failed to generate API key"}, status_code=500)
                
                # Mark request as approved
                req['status'] = 'approved'
                req['approved_by'] = user['client_name']
                req['approved_at'] = datetime.now().isoformat()
                
                with open("json/api_key_requests.json", "w") as f:
                    json.dump(requests, f, indent=4)
                
                logger.info(f"✅ Admin {user['client_name']} approved API key for: {req['username']}")
                return {"success": True, "message": f"API key granted to {req['username']}"}
        
        return JSONResponse({"error": "Request not found"}, status_code=404)
    except Exception as e:
        logger.error(f"❌ Error approving API key request: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/api-key-requests/deny")
async def deny_api_key_request(data: ApiKeyRequestActionRequest, user = Depends(get_current_user)):
    """Deny API key request (admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    import json
    from datetime import datetime
    
    try:
        with open("json/api_key_requests.json", "r") as f:
            requests = json.load(f)
        
        for req in requests:
            if req['id'] == data.request_id and req['status'] == 'pending':
                # Mark request as denied
                req['status'] = 'denied'
                req['denied_by'] = user['client_name']
                req['denied_at'] = datetime.now().isoformat()
                
                with open("json/api_key_requests.json", "w") as f:
                    json.dump(requests, f, indent=4)
                
                logger.info(f"❌ Admin {user['client_name']} denied API key for: {req['username']}")
                return {"success": True, "message": f"Request from {req['username']} denied"}
        
        return JSONResponse({"error": "Request not found"}, status_code=404)
    except Exception as e:
        logger.error(f"❌ Error denying API key request: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ============= MUSIC CONTROL ENDPOINTS =============

@app.post("/api/queue/remove/{request_id}")
async def remove_from_queue(request_id: int, user = Depends(get_current_user)):
    """Remove request from queue (Admin/DJ or requester only)"""
    # Check if queue remove feature is enabled
    if not SettingsManager.is_enabled("api_queue_remove"):
        return JSONResponse({"error": "Queue remove feature is currently disabled"}, status_code=403)
    
    from config.requestHandler import requestHandler
    
    result = await requestHandler.remove_request_by_index(
        request_id, 
        user['client_name'],
        moderator=(user['role'] in ["admin", "dj"])
    )
    
    if "error" in result:
        return JSONResponse({"error": result}, status_code=403 if "701" in result else 404)
    
    # Broadcast queue update to all clients
    requests = requestHandler.load_requests()
    queue_data = [{"id": int(k), **v} for k, v in requests.items()]
    await broadcast_queue_update(queue_data)
    
    logger.info(f"✅ {user['client_name']} removed request #{request_id}")
    return {"success": True, "message": result}

@app.post("/api/queue/move-top/{request_id}")
async def move_to_top(request_id: int, user = Depends(get_current_user)):
    """Move request to top of queue (Admin/DJ only)"""
    # Check if move to top feature is enabled
    if not SettingsManager.is_enabled("api_queue_move_top"):
        return JSONResponse({"error": "Move to top feature is currently disabled"}, status_code=403)
    
    if user['role'] not in ["admin", "dj"]:
        return JSONResponse({"error": "Unauthorized: Admin or DJ access required"}, status_code=403)
    
    from config.requestHandler import requestHandler
    
    requests = requestHandler.load_requests()
    request_key = str(request_id)
    
    if request_key not in requests:
        return JSONResponse({"error": "Request not found"}, status_code=404)
    
    # Get the request to move
    target_request = requests[request_key]
    
    # Remove it from current position
    del requests[request_key]
    
    # Rebuild with target at position 1
    new_requests = {"1": {**target_request, "id": 1}}
    new_id = 2
    for key in sorted(requests.keys(), key=int):
        new_requests[str(new_id)] = {**requests[key], "id": new_id}
        new_id += 1
    
    requestHandler.save_requests(new_requests)
    
    # Broadcast queue update to all clients
    queue_data = [{"id": int(k), **v} for k, v in new_requests.items()]
    await broadcast_queue_update(queue_data)
    
    logger.info(f"✅ {user['client_name']} moved request #{request_id} to top")
    return {"success": True, "message": f"Request moved to top"}

@app.post("/api/skip")    
async def api_skip_song(user = Depends(get_current_user)):
    """Skip current song (DJ and Admin only), checking for mute status."""
    
    # Check if skip feature is enabled
    if not SettingsManager.is_enabled("api_skip"):
        return JSONResponse({"error": "Skip feature is currently disabled"}, status_code=403)
    
    # 1. Check Role Permission (Existing Logic)
    if user['role'] not in ["dj", "admin"]:
        # We must retrieve the username from the user object here, as it's needed for the mute check.
        username = user.get('username') 
        
        # 2. 🔇 CHECK MUTE STATUS (NEW LOGIC)
        # Only non-DJ/non-admin users can be muted from interaction features.
        if username and ClientActionsManager.is_muted(username):
            logger.warning(f"❌ Skip failed: Muted user '{username}' attempted skip.")
            # Return specific error message for the frontend
            return JSONResponse({
                "error": "Access denied. You are currently muted and cannot skip songs."
            }, status_code=403)
        
        # If they are not DJ/Admin AND not muted, they still fail the permission check.
        return JSONResponse({"error": "Unauthorized: DJ or Admin access required"}, status_code=403)


    try:
        from config.BG_process_status import skip
        skip.skip_status = True
        logger.info(f"⏭️ Song skipped by: {user['client_name']} (Role: {user['role']})")

        # Broadcast skip notification to all clients immediately
        skip_notification = {
            "type": "skip",
            "message": f"Song skipped by {user['client_name']}",
            "timestamp": time.time()
        }
        logger.info(f"📣 Broadcasted skip notification to all clients")

        return {"success": True, "message": "Song skipped"}
    except Exception as e:
        logger.error(f"❌ Skip error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

class PlayRequest(BaseModel):
    track_id: str
    title: str
    artist: str
    album: str = ""
    duration: int = 0
    album_art: str = ""
    youtube_url: str = ""  # Optional YouTube URL for manual downloads
    requester_name: Optional[str] = None

@app.post("/api/play")
async def api_add_to_queue(data: PlayRequest, user = Depends(get_current_user)):
    """Add song to request queue"""
    # Check if play request feature is enabled
    if not SettingsManager.is_enabled("api_play"):
        return JSONResponse({"error": "Song requests are currently disabled"}, status_code=403)
    
    from config.request_adder import requestAdder
    import json
    
    final_requester_name = data.requester_name if data.requester_name else user['client_name']
    
    authenticated_username = user.get('username')
    user_role = user.get('role')
    
    if user_role not in ["dj", "admin"] and authenticated_username:
        if ClientActionsManager.is_muted(authenticated_username):
            logger.warning(f"❌ Request failed: Muted user '{authenticated_username}' attempted direct song request.")
            return JSONResponse({
                "error": "Access denied. You are currently muted and cannot request songs."
            }, status_code=403)

    try:
        # Add song to queue using the correct request_maker signature
        result = await requestAdder.request_maker(
            song_id=data.track_id,
            requester=user['client_name'],
            app="Web App",
            youtube_url=data.youtube_url  # Pass YouTube URL if provided
        )

        # Check if result is an error string (starts with "error")
        if isinstance(result, str) and result.startswith("error"):
            logger.warning(f"❌ Song request rejected: {result}")
            return JSONResponse({"error": result}, status_code=400)
        elif isinstance(result, str):
            # Other string errors (like "Unknown app")
            logger.warning(f"❌ Song request failed: {result}")
            return JSONResponse({"error": result}, status_code=400)
        elif isinstance(result, dict):
            # Success - result is the song_data dict
            logger.info(f"✅ Song added to queue by {user['client_name']}: {data.title} - {data.artist}")

            # Read updated queue using the proper handler
            from config.requestHandler import requestHandler
            from dataclasses import asdict
            queue = requestHandler.get_requests()
            queue_data = [asdict(song) for song in queue] if queue else []

            # Broadcast queue update to all clients
            await broadcast_queue_update(queue_data)

            return {"success": True, "message": "Song added to queue"}
        else:
            return JSONResponse({"error": "Failed to add song to queue"}, status_code=500)
    except Exception as e:
        logger.error(f"❌ Error adding song to queue: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

class YouTubePlayRequest(BaseModel):
    youtube_url: str
    requester_name: Optional[str] = None

@app.post("/api/play-youtube")
async def api_add_youtube_to_queue(data: YouTubePlayRequest, user = Depends(get_current_user)):
    """Add song from YouTube URL to request queue (auto-extracts metadata)"""
    # Check if YouTube play feature is enabled
    if not SettingsManager.is_enabled("api_play_youtube"):
        return JSONResponse({"error": "YouTube requests are currently disabled"}, status_code=403)
    
    from config.request_adder import requestAdder
    import yt_dlp
    import json
    from dataclasses import asdict
    from urllib.parse import urlparse
    
    final_requester_name = data.requester_name if data.requester_name else user['client_name']
    
    username = user.get('username')
    user_role = user.get('role')
    
    # Apply mute check only if the user is a standard user (not DJ/Admin)
    if user_role not in ["dj", "admin"] and username:
        if ClientActionsManager.is_muted(username):
            logger.warning(f"❌ Request failed: Muted user '{username}' attempted YouTube request.")
            return JSONResponse({
                "error": "Access denied. You are currently muted and cannot request songs."
            }, status_code=403)

    try:
        logger.info(f"🎥 YouTube request from {user['client_name']}: {data.youtube_url}")

        # Security: Validate YouTube URL domain to prevent SSRF attacks
        try:
            parsed_url = urlparse(data.youtube_url)
            allowed_hosts = ['youtube.com', 'www.youtube.com', 'youtu.be', 'm.youtube.com']
            if parsed_url.netloc.lower() not in allowed_hosts:
                logger.warning(f"❌ Invalid YouTube domain: {parsed_url.netloc}")
                return JSONResponse({"error": "Only YouTube URLs are allowed"}, status_code=400)
        except Exception as url_error:
            logger.warning(f"❌ URL validation error: {url_error}")
            return JSONResponse({"error": "Invalid URL format"}, status_code=400)

        # Extract metadata from YouTube video
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(data.youtube_url, download=False)
            
            # Extract metadata
            title = info.get('title', 'Unknown Title')
            uploader = info.get('uploader', '') or info.get('channel', '') or 'Unknown Artist'
            duration = info.get('duration', 0)
            thumbnail = info.get('thumbnail', '')
            
            # Try to parse artist - song from title if it contains " - "
            artist = uploader
            song_title = title
            if ' - ' in title:
                parts = title.split(' - ', 1)
                artist = parts[0].strip()
                song_title = parts[1].strip()
            
            logger.info(f"📝 Extracted metadata: {song_title} by {artist} ({duration}s)")

        # Create a synthetic track ID from YouTube video ID
        video_id = info.get('id', data.youtube_url.split('v=')[-1].split('&')[0])
        track_id = f"youtube_{video_id}"

        # Add song to queue using request_maker with metadata
        result = await requestAdder.request_maker(
            song_id=track_id,
            requester=final_requester_name,
            app="Web App (YouTube)",
            youtube_url=data.youtube_url,
            title=song_title,
            artist=artist,
            album="YouTube",
            duration=duration,
            albumart=thumbnail
        )

        # Check if result is a dict (success) or string (error)
        if isinstance(result, dict) and "error" not in result:
            logger.info(f"✅ YouTube song added to queue: {song_title} by {artist}")

            # Read updated queue
            from config.requestHandler import requestHandler
            queue = requestHandler.get_requests()
            queue_data = [asdict(song) for song in queue] if queue else []

            # Broadcast queue update to all clients
            await broadcast_queue_update(queue_data)

            return {
                "success": True,
                "title": song_title,
                "artist": artist,
                "message": "Song added to queue from YouTube"
            }
        else:
            # result is an error string or dict with error
            error_msg = result if isinstance(result, str) else result.get("error", "Failed to add song")
            logger.warning(f"⚠️ request_maker returned error: {error_msg}")
            return JSONResponse({"error": error_msg}, status_code=400)

    except Exception as e:
        logger.error(f"❌ YouTube request error: {str(e)}")
        return JSONResponse({"error": f"Failed to extract metadata: {str(e)}"}, status_code=500)

@app.get("/api/playlists")
async def get_playlists(user = Depends(get_current_user)):
    """Get list of Spotify playlists (Admin only)"""
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)

    try:
        from config.config import DJ
        return {"playlists": DJ.playlists}
    except Exception as e:
        logger.error(f"❌ Error loading playlists: {e}")
        return {"playlists": []}

@app.post("/api/playlists")
async def add_playlist(request: Request, user = Depends(get_current_user)):
    """Add Spotify playlist URL (Admin only)"""
    # Check if playlist management feature is enabled
    if not SettingsManager.is_enabled("api_playlists"):
        return JSONResponse({"error": "Playlist management is currently disabled"}, status_code=403)
    
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)

    try:
        data = await request.json()
        playlist_url = data.get("playlist_url", "").strip()

        if not playlist_url or "spotify.com/playlist/" not in playlist_url:
            return JSONResponse({"error": "Invalid Spotify playlist URL"}, status_code=400)

        # Read the config file (working directory is New/)
        config_path = "config/config.py"
        with open(config_path, "r") as f:
            lines = f.readlines()

        # Find the playlists section and add new URL
        new_lines = []
        in_playlists = False
        for i, line in enumerate(lines):
            if "playlists = [" in line:
                in_playlists = True
                new_lines.append(line)
                # Check if next line is the closing bracket
                if i + 1 < len(lines) and "]" in lines[i + 1]:
                    # Empty list, add first item
                    new_lines.append(f'        "{playlist_url}"\n')
                else:
                    # Add to existing list
                    new_lines.append(f'        "{playlist_url}",\n')
            elif in_playlists and "]" in line:
                in_playlists = False
                new_lines.append(line)
            else:
                new_lines.append(line)

        # Write back to file
        with open(config_path, "w") as f:
            f.writelines(new_lines)

        logger.info(f"📝 Admin {user['client_name']} added playlist: {playlist_url}")

        # Broadcast playlist update to all clients
        await broadcast_playlist_update()

        return {"success": True, "message": "Playlist added successfully"}
    except Exception as e:
        logger.error(f"❌ Error adding playlist: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.delete("/api/playlists/{index}")
async def delete_playlist(index: int, user = Depends(get_current_user)):
    """Delete playlist by index (Admin only)"""
    # Check if playlist management feature is enabled
    if not SettingsManager.is_enabled("api_playlists"):
        return JSONResponse({"error": "Playlist management is currently disabled"}, status_code=403)
    
    if user['role'] != "admin":
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)

    try:
        from config.config import DJ

        if index < 0 or index >= len(DJ.playlists):
            return JSONResponse({"error": "Invalid playlist index"}, status_code=400)

        removed_url = DJ.playlists[index]

        # Read the config file (working directory is New/)
        config_path = "config/config.py"
        with open(config_path, "r") as f:
            lines = f.readlines()

        # Find and remove the playlist URL
        new_lines = []
        in_playlists = False
        removed = False
        for line in lines:
            if "playlists = [" in line:
                in_playlists = True
                new_lines.append(line)
            elif in_playlists and "]" in line:
                in_playlists = False
                new_lines.append(line)
            elif in_playlists and removed_url in line and not removed:
                # Skip this line (remove the playlist)
                removed = True
                continue
            else:
                new_lines.append(line)

        # Write back to file
        with open(config_path, "w") as f:
            f.writelines(new_lines)

        logger.info(f"🗑️ Admin {user['client_name']} deleted playlist: {removed_url}")

        # Broadcast playlist update to all clients
        await broadcast_playlist_update()

        return {"success": True, "message": "Playlist deleted successfully"}
    except Exception as e:
        logger.error(f"❌ Error deleting playlist: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/queue")
async def api_get_queue():
    """Get current song request queue"""
    try:
        from config.requestHandler import requestHandler
        from dataclasses import asdict
        queue = requestHandler.get_requests()
        if queue:
            return {"queue": [asdict(song) for song in queue]}
        return {"queue": []}
    except Exception as e:
        logger.error(f"❌ Error fetching queue: {e}")
        return {"queue": []}

@app.get("/api/next-coming")
async def api_get_next_coming():
    """Get next coming song"""
    try:
        import json
        with open("json/next_coming.json", "r") as f:
            next_coming = json.load(f)
            return {"next_coming": next_coming}
    except Exception as e:
        logger.error(f"❌ Error fetching next coming: {e}")
        return {"next_coming": []}
    
@app.get("/next-coming")
async def next_coming(request: Request):
    return await WebAPI.next_coming(request)

@app.get("/api/recommendations")
async def api_get_recommendations(user = Depends(get_current_user)):
    """Get song recommendations based on now playing or last requested song (DJ and Admin only)"""
    if user['role'] not in ["dj", "admin"]:
        return JSONResponse({"error": "Unauthorized: DJ or Admin access required"}, status_code=403)

    try:
        import json
        seed_track_id = None

        # Try to use next coming song first (best for recommendations)
        try:
            with open("json/next_coming.json", "r") as f:
                next_coming = json.load(f)
                if isinstance(next_coming, list) and len(next_coming) > 0:
                    spotify_id = next_coming[0].get("spotifyID", "").strip()
                    if spotify_id:
                        seed_track_id = spotify_id
        except:
            pass

        # If no next coming, use currently playing song
        if not seed_track_id:
            try:
                with open("json/now_playing.json", "r") as f:
                    now_playing = json.load(f)
                    if isinstance(now_playing, list) and len(now_playing) > 0:
                        spotify_id = now_playing[0].get("spotifyID", "").strip()
                        if spotify_id:
                            seed_track_id = spotify_id
            except:
                pass

        # If still no seed, try last requested song
        if not seed_track_id:
            try:
                with open("json/requests.json", "r") as f:
                    queue_data = json.load(f)
                    if queue_data and len(queue_data) > 0:
                        last_key = list(queue_data.keys())[-1]
                        last_song = queue_data[last_key]
                        spotify_id = last_song.get("spotifyID", "").strip()
                        if spotify_id:
                            seed_track_id = spotify_id
            except:
                pass

        if not seed_track_id:
            return JSONResponse({"error": "No seed track available for recommendations. Please add a song from Spotify first."}, status_code=400)

        logger.info(f"🎯 Getting recommendations with seed track: {seed_track_id}")

        # Validate the track exists and get artist info
        from config.DJ import downloader
        track_info = None
        artist_id = None
        try:
            track_info = downloader.sp.track(seed_track_id)
            logger.info(f"✅ Seed track validated: {track_info['name']} by {track_info['artists'][0]['name']}")
            artist_id = track_info['artists'][0]['id'] if track_info.get('artists') else None
        except Exception as track_error:
            logger.error(f"❌ Seed track validation failed: {track_error}")
            return JSONResponse({"error": "The seed track is not available on Spotify. Try requesting a different song."}, status_code=400)

        # Try multiple recommendation strategies
        recommendations = None
        
        # Strategy 1: Try track seed with US market
        try:
            logger.info(f"🎵 Trying track-based recommendations (US market)")
            recommendations = downloader.sp.recommendations(seed_tracks=[seed_track_id], limit=10, market='US')
            logger.info(f"✅ Track recommendations (US) succeeded")
        except Exception as rec_error:
            logger.warning(f"⚠️ Track recommendations (US) failed: {rec_error}")
            
            # Strategy 2: Try track seed without market
            try:
                logger.info(f"🎵 Trying track-based recommendations (no market)")
                recommendations = downloader.sp.recommendations(seed_tracks=[seed_track_id], limit=10)
                logger.info(f"✅ Track recommendations (no market) succeeded")
            except Exception as fallback_error:
                logger.warning(f"⚠️ Track recommendations (no market) failed: {fallback_error}")
                
                # Strategy 3: Try artist seed as fallback
                if artist_id:
                    try:
                        logger.info(f"🎵 Trying artist-based recommendations")
                        recommendations = downloader.sp.recommendations(seed_artists=[artist_id], limit=10)
                        logger.info(f"✅ Artist recommendations succeeded")
                    except Exception as artist_error:
                        logger.error(f"❌ Artist recommendations failed: {artist_error}")
        
        # If all strategies failed
        if not recommendations or not recommendations.get('tracks'):
            logger.error(f"❌ All recommendation strategies failed")
            return JSONResponse({"error": "Unable to get recommendations from Spotify. This track may not have enough data for recommendations."}, status_code=500)

        results = []
        for track in recommendations['tracks']:
            album_art = track['album']['images'][0]['url'] if track['album']['images'] else ""
            duration_ms = track.get('duration_ms', 0)
            duration_sec = duration_ms // 1000
            minutes = duration_sec // 60
            seconds = duration_sec % 60

            results.append({
                "id": track['id'],
                "title": track['name'],
                "artist": ", ".join([artist['name'] for artist in track['artists']]),
                "album": track['album']['name'],
                "duration": f"{minutes}:{seconds:02d}",
                "album_art": album_art
            })

        logger.info(f"✅ Generated {len(results)} recommendations for: {user['client_name']}")
        return {"success": True, "results": results}

    except Exception as e:
        logger.error(f"❌ Recommendations error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

# ============= SETTINGS MANAGEMENT ENDPOINTS =============

@app.get("/api/settings")
async def api_get_settings(user = Depends(get_current_user)):
    """Get all system settings (Admin or DJ with approval)"""
    from config.settings_manager import SettingsManager
    from config.settings_access_manager import SettingsAccessManager
    
    # Admins always have access
    if user['role'] == 'admin':
        settings = SettingsManager.get_all_settings()
        return {"success": True, "settings": settings, "has_access": True}
    
    # DJs need approved access
    if user['role'] == 'dj':
        has_access = SettingsAccessManager.has_access(user['username'])
        if has_access:
            settings = SettingsManager.get_all_settings()
            return {"success": True, "settings": settings, "has_access": True}
        return {"success": False, "has_access": False, "message": "Settings access not approved. Please request access from admin."}
    
    return JSONResponse({"error": "Unauthorized: Admin or DJ access required"}, status_code=403)

@app.post("/api/settings/toggle")
async def api_toggle_setting(request: Request, user = Depends(get_current_user)):
    """Toggle a setting on/off (Admin or DJ with approval)"""
    from config.settings_manager import SettingsManager
    from config.settings_access_manager import SettingsAccessManager
    
    data = await request.json()
    setting_name = data.get("name")
    enabled = data.get("enabled")
    
    if not setting_name or enabled is None:
        return JSONResponse({"error": "Missing setting name or enabled status"}, status_code=400)
    
    # Admins always have access
    if user['role'] == 'admin':
        success = SettingsManager.toggle_setting(setting_name, enabled)
        if success:
            return {"success": True, "message": f"Setting '{setting_name}' updated successfully"}
        return JSONResponse({"error": "Setting not found"}, status_code=404)
    
    # DJs need approved access
    if user['role'] == 'dj':
        has_access = SettingsAccessManager.has_access(user['username'])
        if has_access:
            success = SettingsManager.toggle_setting(setting_name, enabled)
            if success:
                return {"success": True, "message": f"Setting '{setting_name}' updated successfully"}
            return JSONResponse({"error": "Setting not found"}, status_code=404)
        return JSONResponse({"error": "Settings access not approved"}, status_code=403)
    
    return JSONResponse({"error": "Unauthorized: Admin or DJ access required"}, status_code=403)

@app.get("/api/settings/check-access")
async def api_check_settings_access(user = Depends(get_current_user)):
    """Check if DJ has approved settings access"""
    from config.settings_access_manager import SettingsAccessManager
    
    # Admins always have access
    if user['role'] == 'admin':
        return {"has_access": True}
    
    # Check if DJ has approved access
    if user['role'] == 'dj':
        has_access = SettingsAccessManager.has_access(user['username'])
        return {"has_access": has_access}
    
    return {"has_access": False}

@app.post("/api/settings/request-access")
async def api_request_settings_access(request: Request, user = Depends(get_current_user)):
    """DJ requests settings access"""
    from config.settings_access_manager import SettingsAccessManager
    
    if user['role'] != 'dj':
        return JSONResponse({"error": "Only DJs can request settings access"}, status_code=403)
    
    # Check if already has access
    if SettingsAccessManager.has_access(user['username']):
        return {"success": False, "message": "You already have settings access"}
    
    data = await request.json()
    reason = data.get("reason", "")
    
    success = SettingsAccessManager.request_access(user['username'], reason)
    if success:
        return {"success": True, "message": "Access request submitted. Awaiting admin approval."}
    return {"success": False, "message": "You already have a pending request"}

@app.get("/api/settings/access-requests")
async def api_get_access_requests(user = Depends(get_current_user)):
    """Get all pending settings access requests (Admin only)"""
    from config.settings_access_manager import SettingsAccessManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    requests = SettingsAccessManager.get_pending_requests()
    return {"success": True, "requests": requests}

@app.post("/api/settings/access-requests/approve")
async def api_approve_access_request(request: Request, user = Depends(get_current_user)):
    """Approve settings access request (Admin only)"""
    from config.settings_access_manager import SettingsAccessManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    data = await request.json()
    request_id = data.get("request_id")
    response_msg = data.get("response", "")
    
    success = SettingsAccessManager.approve_request(request_id, user['username'], response_msg)
    if success:
        return {"success": True, "message": "Access request approved"}
    return JSONResponse({"error": "Request not found"}, status_code=404)

@app.post("/api/settings/access-requests/deny")
async def api_deny_access_request(request: Request, user = Depends(get_current_user)):
    """Deny settings access request (Admin only)"""
    from config.settings_access_manager import SettingsAccessManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    data = await request.json()
    request_id = data.get("request_id")
    response_msg = data.get("response", "")
    
    success = SettingsAccessManager.deny_request(request_id, user['username'], response_msg)
    if success:
        return {"success": True, "message": "Access request denied"}
    return JSONResponse({"error": "Request not found"}, status_code=404)

# ============= CLIENT MANAGEMENT ENDPOINTS =============

@app.post("/api/clients/ban")
async def api_ban_client(request: Request, user = Depends(get_current_user)):
    """Ban a client (Admin only)"""
    from config.client_actions_manager import ClientActionsManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    data = await request.json()
    username = data.get("username")
    reason = data.get("reason", "")
    
    if not username:
        return JSONResponse({"error": "Missing username"}, status_code=400)
    
    success = ClientActionsManager.ban_client(username, user['username'], reason)
    if success:
        # Disconnect the client if currently connected
        from Websocket.client_manager import ClientManager
        ClientManager.disconnect_user(username)
        return {"success": True, "message": f"User '{username}' has been banned"}
    return JSONResponse({"error": "Failed to ban user"}, status_code=500)

@app.post("/api/clients/unban")
async def api_unban_client(request: Request, user = Depends(get_current_user)):
    """Unban a client (Admin only)"""
    from config.client_actions_manager import ClientActionsManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    data = await request.json()
    username = data.get("username")
    
    if not username:
        return JSONResponse({"error": "Missing username"}, status_code=400)
    
    success = ClientActionsManager.unban_client(username, user['username'])
    if success:
        return {"success": True, "message": f"User '{username}' has been unbanned"}
    return JSONResponse({"error": "Failed to unban user"}, status_code=500)

def calculate_expiry_datetime(duration_str: str) -> Optional[str]:
    """
    Converts a duration string (e.g., '1h', '15m') into an ISO-formatted UTC datetime string.
    Returns None for permanent or invalid duration.
    """
    import re
    if not duration_str:
        return None
        
    duration_str = duration_str.lower().strip()
    
    if duration_str == 'none':
        return None

    match = re.match(r'(\d+)([mhdwa])', duration_str)
    if not match:
        # Invalid format
        return None
    
    value = int(match.group(1))
    unit = match.group(2)
    
    # Map unit to timedelta arguments
    if unit == 'm': # minutes
        delta = timedelta(minutes=value)
    elif unit == 'h': # hours
        delta = timedelta(hours=value)
    elif unit == 'd': # days
        delta = timedelta(days=value)
    elif unit == 'w': # weeks
        delta = timedelta(weeks=value)
    else:
        # Should be caught by regex, but for safety
        return None
        
    # Calculate future datetime in UTC and format as ISO string
    expires_at = datetime.utcnow() + delta
    return expires_at.isoformat()

@app.post("/api/clients/mute")
async def api_mute_client(request: Request, user = Depends(get_current_user)):
    """Mute a client (Admin only)"""
    from config.client_actions_manager import ClientActionsManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    expires_at_iso = calculate_expiry_datetime(request.duration)
    
    data = await request.json()
    username = data.get("username")
    reason = data.get("reason", "")
    duration = data.get("duration", "")
    
    expires_at_iso = calculate_expiry_datetime(duration)
    
    if not username:
        return JSONResponse({"error": "Missing username"}, status_code=400)
    
    success = ClientActionsManager.mute_client(username, user['username'], reason, expires_at_iso)
    if success:
        return {"success": True, "message": f"User '{username}' has been muted"}
    return JSONResponse({"error": "Failed to mute user"}, status_code=500)

@app.post("/api/clients/kick")
async def api_kick_client(request: Request, user = Depends(get_current_user)):
    """Kick a client (Admin only)"""
    from config.client_actions_manager import ClientActionsManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    data = await request.json()
    username = data.get("username")
    reason = data.get("reason", "")
    
    if not username:
        return JSONResponse({"error": "Missing username"}, status_code=400)
    
    success = ClientActionsManager.kick_client(username, user['username'], reason)
    if success:
        # Disconnect the client
        from Websocket.client_manager import ClientManager
        ClientManager.disconnect_user(username)
        return {"success": True, "message": f"User '{username}' has been kicked"}
    return JSONResponse({"error": "Failed to kick user"}, status_code=500)

@app.get("/api/clients/banned")
async def api_get_banned_clients(user = Depends(get_current_user)):
    """Get all banned clients (Admin only)"""
    from config.client_actions_manager import ClientActionsManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    banned = ClientActionsManager.get_banned_clients()
    return {"success": True, "banned": banned}

@app.get("/api/clients/actions")
async def api_get_client_actions(user = Depends(get_current_user)):
    """Get all client actions (Admin only)"""
    from config.client_actions_manager import ClientActionsManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    actions = ClientActionsManager.get_all_actions()
    return {"success": True, "actions": actions}

@app.get("/api/clients/connected")
async def api_get_connected_clients(user = Depends(get_current_user)):
    """Get all currently connected clients (Admin only)"""
    from Websocket.client_manager import ClientManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    clients = ClientManager.list_all()
    connected = [{
        "username": data["client_name"],
        "connection_id": data.get("connection_id", ""),
        "connected_at": data.get("connected_at", "")
    } for key, data in clients.items()]
    
    connected = [{
        "username": data["client_name"],
        "connection_id": data.get("connection_id", ""),
        "connected_at": data.get("connected_at", ""),
        "role": data.get("role", "UNKNOWN") 
    } for key, data in clients.items()]
    
    return {"success": True, "connected": connected}

# ============= PROMOTIONS ENDPOINTS =============

@app.post("/api/promotions/upload")
async def upload_promotion(
    name: str = Form(...),
    description: str = Form(...),
    promoter: str = Form(...),
    from_datetime: str = Form(...),
    to_datetime: str = Form(...),
    audio_file: UploadFile = File(...),
    user = Depends(get_current_user)
):
    """Upload a new promotional audio (Admin/DJ only)"""
    from config.PromotionManager import PromotionManager
    
    # Check authorization
    if user['role'] not in ['admin', 'dj']:
        return JSONResponse({"error": "Unauthorized: Admin or DJ access required"}, status_code=403)
    
    # Validate file type
    if not audio_file.filename.endswith('.mp3'):
        return JSONResponse({"error": "Only MP3 files are allowed"}, status_code=400)
    
    # Validate datetime format
    try:
        datetime.fromisoformat(from_datetime)
        datetime.fromisoformat(to_datetime)
    except ValueError:
        return JSONResponse({"error": "Invalid datetime format. Use ISO format (YYYY-MM-DDTHH:MM:SS)"}, status_code=400)
    
    # Save uploaded file temporarily
    temp_path = f"promotions/temp_{audio_file.filename}"
    os.makedirs("promotions", exist_ok=True)
    
    with open(temp_path, "wb") as f:
        content = await audio_file.read()
        f.write(content)
    
    # Add promotion using PromotionManager
    promo_manager = PromotionManager()
    promo_metadata = promo_manager.add_promotion(
        name=name,
        description=description,
        promoter=promoter,
        from_datetime=from_datetime,
        to_datetime=to_datetime,
        audio_path=temp_path
    )
    
    logger.info(f"📢 New promo uploaded: {name} by {promoter} (uploaded by {user['username']})")
    return {"success": True, "message": "Promotion uploaded successfully", "promo": promo_metadata}

@app.get("/api/promotions")
async def get_promotions(user = Depends(get_current_user)):
    """Get all promotions (Admin/DJ only)"""
    from config.PromotionManager import PromotionManager
    
    if user['role'] not in ['admin', 'dj']:
        return JSONResponse({"error": "Unauthorized: Admin or DJ access required"}, status_code=403)
    
    promo_manager = PromotionManager()
    all_promos = promo_manager.get_all_promotions()
    active_promos = promo_manager.get_active_promotions()
    
    return {
        "success": True,
        "all": all_promos,
        "active": active_promos,
        "total": len(all_promos),
        "active_count": len(active_promos)
    }

@app.delete("/api/promotions/{promo_id}")
async def delete_promotion(promo_id: str, user = Depends(get_current_user)):
    """Delete a promotion (Admin only)"""
    from config.PromotionManager import PromotionManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    promo_manager = PromotionManager()
    success = promo_manager.delete_promotion(promo_id)
    
    if success:
        logger.info(f"🗑️ Promo deleted by {user['username']}: {promo_id}")
        return {"success": True, "message": "Promotion deleted successfully"}
    
    return JSONResponse({"error": "Promotion not found"}, status_code=404)

@app.post("/api/promotions/cleanup")
async def cleanup_expired_promotions(user = Depends(get_current_user)):
    """Manually trigger cleanup of expired promotions (Admin only)"""
    from config.PromotionManager import PromotionManager
    
    if user['role'] != 'admin':
        return JSONResponse({"error": "Unauthorized: Admin access required"}, status_code=403)
    
    promo_manager = PromotionManager()
    deleted_count = promo_manager.cleanup_expired_promotions()
    
    return {
        "success": True,
        "message": f"Cleaned up {deleted_count} expired promotion(s)",
        "deleted_count": deleted_count
    }

@app.get("/now-playing")
async def now_playing(request: Request):
    return await WebAPI.now_playing(request)

@app.get("/queue")
async def get_queue(request: Request):
    return await WebAPI.get_queue(request)

@app.get("/blocklist")
async def get_blocklist(request: Request):
    return await WebAPI.blocklist(request)

@app.post("/skip")
async def skip_current_song(request: Request):
    return await WebAPI.skip_current_song(request)

@app.post("/unblock")
async def remove_blocked_song(request: Request):
    return await WebAPI.remove_blocked_song(request)

@app.post("/block")
async def block_current_song(request: Request):
    return await WebAPI.block_current_song(request)

@app.post("/remove")
async def remove_song(request: Request):
    return await WebAPI.remove_song(request)

@app.post("/play")
async def play_song(request: Request):
    return await WebAPI.play_song(request)

@app.post("/reloadall")
async def reloadall(request: Request):
    return await WebAPI.reloadall(request)

# ============= WEBSOCKET ENDPOINT =============

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_ip = websocket.client.host if websocket.client else "unknown"
    logger.info(f"🔌 WebSocket connection attempt from {client_ip}")

    await websocket.accept()
    try:
        # Initialize session metadata for the client connection
        connection_id = generate_connection_id()

        data = await websocket.receive_json()
        api_key = data.get("api_key")
        jwt_token = data.get("token")

        client_info = None
        auth_key = None

        if jwt_token:
            client_info = get_client_info_by_jwt(jwt_token)
            auth_key = jwt_token
        elif api_key:
            client_info = get_client_info_by_key(api_key)
            auth_key = api_key
            if not is_api_key_valid(api_key, client_info):
                client_info = None

        if not client_info or not auth_key:
            logger.warning(f"❌ WebSocket auth failed from {client_ip}: Invalid credentials")
            await websocket.send_json({"error": "Unauthorized: Invalid credentials"})
            await websocket.close()
            return

        logger.info(f"✅ WebSocket authenticated: {client_info.client_name} (Role: {'DJ' if client_info.is_DJ else 'User'}) - Connection ID: {connection_id}")

        # Store connection in ClientManager
        ClientManager.add(auth_key, websocket, client_info, connection_id)
        
        # Send auth_success message to client
        await websocket.send_json({
            "action": "auth_success",
            "type": "notification",
            "message": "Authentication successful"
        })

        metadata = SessionMetadata(
            client_id=client_info.client_id,
            client_name=client_info.client_name,
            rate_limits={"global": (10, 1.0)},
            connection_id=connection_id
        )

        await PewHits.on_start(websocket, "notification", asdict(metadata))

        await PewHits.on_start_now_playing(websocket, "notification")  # Call the now_playing method

        while True:
            # Wait for client messages
            data_dict = await websocket.receive_json()
            action = data_dict.get("action")
            client_ip = websocket.client.host

            if action == "queue":
                response = await send_rate_limited_response(websocket, "/queue", client_ip)
                if response:
                    return response
                await PewHits.queue(websocket, data_dict, "response")  # Call the queue method
            elif action == "now":
                response = await send_rate_limited_response(websocket, "/now", client_ip)
                if response:
                    return response
                await PewHits.now_playing(websocket, data_dict, "response")  # Call the now_playing method
            elif action == "next":
                response = await send_rate_limited_response(websocket, "/next", client_ip)
                if response:
                    return response
                await PewHits.next_coming(websocket, data_dict, "response")
            elif action == "blocklist":
                response = await send_rate_limited_response(websocket, "/blocklist", client_ip)
                if response:
                    return response
                await PewHits.blocklist(websocket, data_dict, "response")
            elif action == "skip":
                response = await send_rate_limited_response(websocket, "/skip", client_ip)
                if response:
                    return response
                data_dict["DJ"] = client_info.is_DJ
                await PewHitsServer.skip_current_song(websocket, data_dict, "response")
            elif action == "unblock":
                response = await send_rate_limited_response(websocket, "/unblock", client_ip)
                if response:
                    return response
                data_dict["DJ"] = client_info.is_DJ
                await PewHitsServer.remove_blocked_song(websocket, data_dict, "response")
            elif action == "block":
                response = await send_rate_limited_response(websocket, "/block", client_ip)
                if response:
                    return response
                data_dict["DJ"] = client_info.is_DJ
                await PewHitsServer.block_current_song(websocket, data_dict, "response")
            elif action == "remove":
                response = await send_rate_limited_response(websocket, "/remove", client_ip)
                if response:
                    return response
                await PewHitsServer.remove_song(websocket, data_dict, "response")
            elif action == "play":
                response = await send_rate_limited_response(websocket, "/play", client_ip)
                if response:
                    return response
                await PewHitsServer.play_song(websocket, data_dict, "response")
            elif action == "reloadall":
                response = await send_rate_limited_response(websocket, "/reloadall", client_ip)
                if response:
                    return response
                await PewHitsServer.reloadall(websocket, data_dict, "response")
            elif action == "KeepAliveRequest":
                ClientManager.update_keepalive(auth_key)
                await websocket.send_json({"action": "KeepAliveResponse", "type": "notification", "status": "ok"})

    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket disconnected cleanly: {client_ip}")
        ClientManager.remove(websocket)
    except Exception as e:
        logger.error(f"💥 WebSocket error from {client_ip}: {e}")
    finally:
        try:
            if websocket.client_state.name != "DISCONNECTED":
                await websocket.close()
            ClientManager.remove(websocket)
            logger.info(f"🔌 WebSocket connection closed: {client_ip}")
        except Exception:
            # Silently handle already closed connections
            pass

async def broadcast_song(event: Literal["now_playing", "next_coming"], action: Literal["notification", "response"], song):
    """Universal broadcaster for song updates (handles both now_playing and next_coming)."""
    clients = ClientManager.list_all()
    client_count = len(clients)
    title = getattr(song, "title", None) if song else None

    logger.info(f"📣 Broadcasting {event.upper()} to {client_count} client(s): {title or 'None'}")

    for api_key, client_data in clients.items():
        websocket = client_data["websocket"]
        client_name = client_data.get("client_info", {}).get("client_name", "Unknown")

        try:
            if websocket.application_state.name != "CONNECTED":
                continue

            # Normalize song data
            if song is None:
                song_data = None
            elif isinstance(song, dict):
                song_data = song
            elif is_dataclass(song):
                song_data = asdict(song)
            elif hasattr(song, "__dict__"):
                song_data = song.__dict__
            else:
                song_data = str(song)

            await websocket.send_json({
                "action": event,
                "type": action,
                "data": song_data
            })

            logger.debug(f"  ✅ Sent {event} update to {client_name}")

        except Exception as e:
            logger.error(f"  ❌ Error broadcasting {event} to {client_name}: {e}")
            
async def broadcast_listeners_update(listener_count: int):
    """Broadcast current listener count to all connected clients"""
    clients = ClientManager.list_all()
    client_count = len(clients)
    logger.info(f"📣 Broadcasting LISTENERS UPDATE ({listener_count}) to {client_count} client(s)")

    for api_key, client_data in clients.items():
        websocket = client_data["websocket"]
        try:
            if websocket.application_state.name == "CONNECTED":
                await websocket.send_json({
                    "action": "listeners",
                    "type": "notification",
                    "data": {
                        "listeners": str(listener_count)
                    }
                })
        except Exception as e:
            logger.error(f"  ❌ Error broadcasting listeners update: {e}")
            
async def broadcast_queue_update(queue_data):
    """Broadcast queue update to all connected clients"""
    clients = ClientManager.list_all()
    client_count = len(clients)
    logger.info(f"📣 Broadcasting QUEUE UPDATE to {client_count} client(s)")

    for api_key, client_data in clients.items():
        websocket = client_data["websocket"]
        try:
            if websocket.application_state.name == "CONNECTED":
                await websocket.send_json({
                    "action": "queue",
                    "type": "notification",
                    "data": queue_data
                })
        except Exception as e:
            logger.error(f"  ❌ Error broadcasting queue update: {e}")

async def broadcast_playlist_update():
    """Broadcast playlist update notification to all connected clients"""
    clients = ClientManager.list_all()
    client_count = len(clients)
    logger.info(f"📣 Broadcasting PLAYLIST UPDATE to {client_count} client(s)")

    try:
        from config.config import DJ
        playlists = DJ.playlists
    except:
        playlists = []

    for api_key, client_data in clients.items():
        websocket = client_data["websocket"]
        try:
            if websocket.application_state.name == "CONNECTED":
                await websocket.send_json({
                    "action": "playlists_updated",
                    "type": "notification",
                    "data": {"playlists": playlists}
                })
        except Exception as e:
            logger.error(f"  ❌ Error broadcasting playlist update: {e}")

async def start_ws_server_async():
    def start():
        logger.info(f"🚀 Starting unified FastAPI server on 0.0.0.0:{Server.PORT} (Dashboard + WebSocket + API)")
        uvicorn.run(app, host="0.0.0.0", port=Server.PORT, log_level="info")

    threading.Thread(target=start, daemon=True).start()

if __name__ == "__main__":
    logger.info(f"🚀 Starting unified FastAPI server on 0.0.0.0:{Server.PORT} (Dashboard + WebSocket + API)")
    uvicorn.run(app, host="0.0.0.0", port=Server.PORT, log_level="info")