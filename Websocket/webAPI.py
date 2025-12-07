from datetime import datetime, timedelta
import asyncio, json, threading
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional
from dataclasses import asdict
import importlib, sys, os
from Websocket.models import ClientInfo
from config.songHandler import songHandler
from config.requestHandler import requestHandler
from config.request_adder import requestAdder
from config.BG_process_status import songDownloader
from config.blocker import blocker


RATE_LIMITS = {
    "/play": {"ip": (30, 60), "user": (20, 1200)},
    "/now-playing": (30, 120),  # 30 requests/2 minute
    "/next-coming": (30, 120),  # 30 requests/2 minute
    "/queue": (30, 120),  # 30 requests/2 minute
    "/remove": (30, 120),  # 30 requests/2 minute
    "/block": (30, 120),  # 30 requests/2 minute
    "/unblock": (30, 120),  # 30 requests/2 minute
    "blocklist": (30, 120),
    "/skip": (1, 30),
}

rate_limit_data = {
    "/play": {"ip": {}, "user": {}},
    "/now-playing": {},
    "/next-coming": {},
    "/queue": {},
    "/remove": {},
    "/block": {},
    "/unblock": {},
    "blocklist": {},
    "/skip": {},
}


def is_rate_limited_(endpoint, identifier, is_user=False):
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

def rate_limiter(ip_address, endpoint, username=None):
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

file_path = "json/auth_clients.json"

def load_clients_info(file_path: str):
    # Load authorized clients
    with open(file_path, "r") as file:
        return json.load(file)
    
def get_client_info_by_key(api_key: str) -> Optional[ClientInfo]:
    AUTHORIZED_CLIENTS = load_clients_info(file_path)
    
    for client in AUTHORIZED_CLIENTS.values():
        if client.get("client_auth_key") == api_key:
            return ClientInfo(
                client_id=client.get("client_id"),
                client_name=client.get("client_name"),
                api_key=api_key,
                is_DJ=client.get("is_DJ", False),
                client_description=client.get("client_description")
            )
    
    return None

def is_api_key_valid(api_key: str, client_info: ClientInfo) -> bool:
    if client_info.api_key == api_key:
        return True
    return False
    
    
class WebAPI:
    async def next_coming(request: Request):
        """Retrieve the next upcoming song."""
        client_ip = request.client.host

        allowed, retry_after = rate_limiter(client_ip, "/next-coming")
        if not allowed:
            return JSONResponse(
                content={"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": f"{int(retry_after)}"}
            )
            
        api_key = request.headers.get("x-api-key")
        client_info = get_client_info_by_key(api_key)
        
        
        if not api_key or not is_api_key_valid(api_key, client_info) or not client_info:
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid API key.")

        next_coming = songHandler.get_next_coming_data()
        return JSONResponse(content={"success": True, "next_coming": asdict(next_coming)}, status_code=200)
    
    async def now_playing(request: Request):
        """Retrieve the currently playing song."""
        client_ip = request.client.host

        allowed, retry_after = rate_limiter(client_ip, "/now-playing")
        if not allowed:
            return JSONResponse(
                content={"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": f"{int(retry_after)}"}
            )
        
        api_key = request.headers.get("x-api-key")
        client_info = get_client_info_by_key(api_key)
        
        
        if not api_key or not is_api_key_valid(api_key, client_info) or not client_info:
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid API key.")

        now_playing = songHandler.get_now_playing_data()
        if not now_playing:
            print("❌ now_playing is None!")
            return JSONResponse(content={"success": False, "now_playing": None}, status_code=200)
        
        print(asdict(now_playing))
        return JSONResponse(content={"success": True, "now_playing": asdict(now_playing)}, status_code=200)
    
    async def get_queue(request: Request):
        """Retrieve the current song queue."""
        client_ip = request.client.host

        allowed, retry_after = rate_limiter(client_ip, "/queue")
        if not allowed:
            return JSONResponse(
                content={"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": f"{int(retry_after)}"}
            )
        
        api_key = request.headers.get("x-api-key")
        client_info = get_client_info_by_key(api_key)
        
        
        if not api_key or not is_api_key_valid(api_key, client_info) or not client_info:
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid API key.")

        requests = requestHandler.get_requests()
        return JSONResponse(content={"success": True, "queue": asdict(requests)}, status_code=200)
    
    async def skip_current_song(request: Request):
        client_ip = request.client.host

        allowed, retry_after = rate_limiter(client_ip, "/skip")
        if not allowed:
            return JSONResponse(
                content={"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": f"{int(retry_after)}"}
            )

        api_key = request.headers.get("x-api-key")
        client_info = get_client_info_by_key(api_key)

        if not api_key or not is_api_key_valid(api_key, client_info) or not client_info:
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid API key.")
        
        if not client_info.is_DJ:
            return JSONResponse(content={"error": "You are not authorized to skip songs."}, status_code=806)

        if songDownloader.song_downloader:
            return JSONResponse(
                content={"error": "Cannot skip songs when next song is being download is in processing."},
                status_code=805
            )

        now_playing_data = songHandler.get_now_playing_data()

        if not now_playing_data:
            return JSONResponse(content={"error": "No song is currently playing"}, status_code=404)

        duration = (now_playing_data.position or 0)

        from config.BG_process_status import skip
        skip.skip_status = True

        return JSONResponse(content={"success": True, "message": "Skipping current song"}, status_code=200)
    
    async def blocklist(request: Request):
        """Retrieve the blocklisted songs data."""
        client_ip = request.client.host
        allowed, retry_after = rate_limiter(client_ip, "/blocklist")

        if not allowed:
            return JSONResponse(
                content={"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": f"{int(retry_after)}"}
            )

        api_key = request.headers.get("x-api-key")
        client_info = get_client_info_by_key(api_key)
        
        
        if not api_key or not is_api_key_valid(api_key, client_info) or not client_info:
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid API key.")

        blocked_songs = blocker.list_blocked_songs()

        return JSONResponse(content={"success": True, "blocklist": asdict(blocked_songs)}, status_code=200)
    
    async def remove_blocked_song(request: Request):
        client_ip = request.client.host
        try:
            data = await request.json()
        except Exception:
            return JSONResponse(content={"error": "Invalid or missing JSON data"}, status_code=400)

        index = data.get("index")
        moderator = str(data.get("moderator")).lower() == "true"
        app_name = data.get("app")
        
        app_id = requestAdder.get_app_id(app_name) if app_name else None

        if app_name and app_id is None:
            return JSONResponse(content={"error": f"Unknown app: {app_name}"}, status_code=608)

        allowed, retry_after = rate_limiter(client_ip, "/unblock")
        if not allowed:
            return JSONResponse(
                content={"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": f"{int(retry_after)}"}
            )

        api_key = request.headers.get("x-api-key")
        client_info = get_client_info_by_key(api_key)
        
        if not api_key or not is_api_key_valid(api_key, client_info) or not client_info:
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid API key.")
        
        if not client_info.is_DJ:
            return JSONResponse(content={"error": "You are not authorized to unblock songs."}, status_code=808)

        if not moderator:
            return JSONResponse(content={"error": "You are not authorized to remove this blocked song."}, status_code=703)

        try:
            song_resp = blocker.remove_song_by_index(index)

            if isinstance(song_resp, str):
                if "error 801" in song_resp:
                    return JSONResponse(content={"error": "Provide at least one valid song ID (Spotify or YouTube)."}, status_code=801)
                elif "error 804" in song_resp:
                    return JSONResponse(content={"error": "Blocked song with index provided does not exist."}, status_code=804)

            return JSONResponse(content={"success": True, "data": song_resp}, status_code=200)

        except Exception as e:
            return JSONResponse(content={"error": f"An unexpected error occurred: {str(e)}"}, status_code=500)
        
    async def block_current_song(request: Request):
        client_ip = request.client.host
        try:
            data = await request.json()
        except Exception:
            return JSONResponse(content={"error": "Invalid or missing JSON data"}, status_code=400)

        blocker = data.get("blocker")
        moderator = str(data.get("moderator")).lower() == "true"
        app_name = data.get("app")

        app_id = requestAdder.get_app_id(app_name) if app_name else None

        if app_name and app_id is None:
            return JSONResponse(content={"error": f"Unknown app: {app_name}"}, status_code=608)

        allowed, retry_after = rate_limiter(client_ip, "/block")
        if not allowed:
            return JSONResponse(
                content={"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": f"{int(retry_after)}"}
            )

        api_key = request.headers.get("x-api-key")
        client_info = get_client_info_by_key(api_key)
        
        if not api_key or not is_api_key_valid(api_key, client_info) or not client_info:
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid API key.")
        
        if not client_info.is_DJ:
            return JSONResponse(content={"error": "You are not authorized to block songs."}, status_code=807)

        if not moderator:
            return JSONResponse(content={"error": "You are not authorized to remove this blocked song."}, status_code=703)
        
        if songDownloader.song_downloader:
            return JSONResponse(
                content={"error": "Cannot block songs when next song is being downloaded."},
                status_code=805
            )

        now_playing = songHandler.get_now_playing_data()

        if not now_playing:
            return JSONResponse(content={"error": "No song is currently playing"}, status_code=404)

        ID = now_playing.ID
        title = now_playing.title
        artist = now_playing.artist
        album = now_playing.album

        try:
            song_resp = blocker.add_song(ID, None, title, artist, album, blocker)

            if isinstance(song_resp, str):
                if "error 802" in song_resp:
                    return JSONResponse(content={"error": "This song is already blocked."}, status_code=802)
                elif "error 801" in song_resp:
                    return JSONResponse(content={"error": "Provide at least one valid song ID (Spotify or YouTube)."}, status_code=801)

            from config.BG_process_status import skip
            skip.skip_status = True

            return JSONResponse(content={"success": True, "data": song_resp}, status_code=200)

        except Exception as e:
            return JSONResponse(content={"error": f"An unexpected error occurred: {str(e)}"}, status_code=500)
        
    async def remove_song(request: Request):
        client_ip = request.client.host

        try:
            data = await request.json()
        except Exception:
            return JSONResponse(content={"error": "Invalid or missing JSON data"}, status_code=400)

        song_index = data.get("index")
        requester = data.get("requester")
        moderator = str(data.get("moderator")).lower() == "true"
        app_name = data.get("app")

        app_id = requestAdder.get_app_id(app_name) if app_name else None

        if app_name and app_id is None:
            return JSONResponse(content={"error": f"Unknown app: {app_name}"}, status_code=608)

        api_key = request.headers.get("x-api-key")
        client_info = get_client_info_by_key(api_key)
        
        if not api_key or not is_api_key_valid(api_key, client_info) or not client_info:
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid API key.")

        allowed, retry_after = rate_limiter(client_ip, "/remove")
        if not allowed:
            return JSONResponse(
                content={"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": f"{int(retry_after)}"}
            )

        try:
            song_data = await requestHandler.remove_request_by_index(song_index, requester, moderator)

            if isinstance(song_data, str):
                if "error 701" in song_data:
                    return JSONResponse(content={"error": "You are not authorized to remove this song request."}, status_code=701)
                elif "error 702" in song_data:
                    return JSONResponse(content={"error": "Request with index provided does not exist."}, status_code=702)
                elif "error 801" in song_data:
                    return JSONResponse(content={"error": "Provide at least one valid song ID (Spotify or YouTube)."}, status_code=801)

            return JSONResponse(content={"success": True, "data": song_data}, status_code=200)

        except Exception as e:
            return JSONResponse(content={"error": f"An unexpected error occurred: {str(e)}"}, status_code=500)
        
    
    async def play_song(request: Request):
        client_ip = request.client.host

        try:
            data = await request.json()
        except Exception:
            return JSONResponse(content={"error": "Invalid or missing JSON data"}, status_code=400)

        song_id = data.get("song_id")
        requester = data.get("requester")
        app_name = data.get("app")

        api_key = request.headers.get("x-api-key")
        client_info = get_client_info_by_key(api_key)
        
        if not api_key or not is_api_key_valid(api_key, client_info) or not client_info:
            raise HTTPException(status_code=403, detail="Unauthorized: Invalid API key.")

        allowed, retry_after = rate_limiter(client_ip, "/play")
        if not allowed:
            return JSONResponse(
                content={"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": f"{int(retry_after)}"}
            )

        exceeded, retry_after_user = is_rate_limited_("/play", requester, is_user=True)
        if exceeded:
            return JSONResponse(
                content={"error": "Requested too many songs within a short time, you can request again after a while."},
                status_code=704,
                headers={"Retry-After": f"{int(retry_after_user)}"}
            )

        try:
            song_data = await requestAdder.request_maker(song_id, requester, app_name)

            if isinstance(song_data, str):
                if "Unknown app" in song_data:
                    return JSONResponse(content={"error": song_data}, status_code=608)
                elif "error 609" in song_data:
                    return JSONResponse(content={"error": "Track recently played."}, status_code=609)
                elif "error 709" in song_data:
                    return JSONResponse(content={"error": "Requested song is blocked."}, status_code=709)
                elif "error 708" in song_data:
                    return JSONResponse(content={"error": "You have already requested this song, and it is waiting in the queue."}, status_code=708)
                elif "error 707" in song_data:
                    return JSONResponse(content={"error": "This song is already in the queue."}, status_code=707)
                elif "error 706" in song_data:
                    return JSONResponse(content={"error": "This song is currently playing."}, status_code=706)
                elif "error 500" in song_data:
                    return JSONResponse(content={"error": "Internal server error. Please try again later."}, status_code=500)
                else:
                    return JSONResponse(content={"error": song_data}, status_code=400)

            # Double-check IP+username-based rate limit
            user_allowed, user_retry_after = rate_limiter(client_ip, "/play", username=requester)
            if not user_allowed:
                return JSONResponse(
                    content={"error": "Requested too many songs within a short time, you can request again after a while."},
                    status_code=704,
                    headers={"Retry-After": f"{int(user_retry_after)}"}
                )

            return JSONResponse(content={"success": True, "data": song_data}, status_code=200)

        except Exception as e:
            return JSONResponse(content={"error": f"An unexpected error occurred: {str(e)}"}, status_code=500)
        
    async def reloadall(request: Request):
        """Reload all data from the JSON files."""
        client_ip = request.client.host

        allowed, retry_after = rate_limiter(client_ip, "/reloadall")
        if not allowed:
            return JSONResponse(
                content={"error": "Too many requests"},
                status_code=429,
                headers={"Retry-After": f"{int(retry_after)}"}
            )
            
        try:
            api_key = request.headers.get("x-api-key")
            client_info = get_client_info_by_key(api_key)
            
            if not api_key or not is_api_key_valid(api_key, client_info) or not client_info:
                raise HTTPException(status_code=403, detail="Unauthorized: Invalid API key.")
            
            reloaded_modules = []
            base_dirs = ["WebAPI", "config", "Websocket"]

            for base in base_dirs:
                for root, dirs, files in os.walk(base):
                    for file in files:
                        if file.endswith(".py") and not file.startswith("__"):
                            module_path = os.path.join(root, file).replace("/", ".").replace("\\", ".").replace(".py", "")
                            if module_path in sys.modules:
                                importlib.reload(sys.modules[module_path])
                                reloaded_modules.append(module_path)
                            else:
                                __import__(module_path)
                                reloaded_modules.append(module_path)                   

            # 📩 Confirmation message
            msg = f"✅ Reloaded {len(reloaded_modules)} modules from src/ & config/. Ready to roll!"
            
            return JSONResponse(content={"success": True, "message": msg}, status_code=200)
        except Exception as e:
            return JSONResponse(content={"error": f"An unexpected error occurred: {str(e)}"}, status_code=500)