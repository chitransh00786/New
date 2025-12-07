# client_manager.py
import time, asyncio
from datetime import datetime

from fastapi import WebSocket
from .models import ClientInfo

class ClientManager:
    connected_clients = {}  # websocket: last_seen timestamp
        
    @classmethod
    def add(cls, api_key: str, websocket: WebSocket, client_info: ClientInfo, connection_id: str):
        current_time = datetime.now()
        
        cls.connected_clients[api_key] = {
            "websocket": websocket,
            "last_seen": current_time,
            "status": "connected",
            "client_id": client_info.client_id,
            "client_name": client_info.client_name,
            "connection_id": connection_id,
            "role": client_info.role, 
            "connected_at": current_time.isoformat() 
        }
        print(f"➕ Client [{client_info.client_name}] connected. Total: {len(cls.connected_clients)}")

    @classmethod
    def remove(cls, websocket: WebSocket):
        for api_key, info in list(cls.connected_clients.items()):
            if info["websocket"] == websocket:
                info["status"] = "disconnected"
                print(f"➖ Client [{cls.connected_clients[api_key]['client_name']}] removed. Total: {len(cls.connected_clients)}")
                del cls.connected_clients[api_key]
                break

    @classmethod
    def update_keepalive(cls, api_key: str):
        if api_key in cls.connected_clients:
            cls.connected_clients[api_key]["last_seen"] = datetime.now()
            cls.connected_clients[api_key]["status"] = "alive"

    @classmethod
    def list_all(cls):
        return cls.connected_clients
    
    @classmethod
    def get_connected_client(cls, api_key: str):
        return cls.connected_clients.get(api_key)

    @classmethod
    def get_last_seen(cls, api_key: str):
        client_info = cls.get_connected_client(api_key)
        return client_info["last_seen"] if client_info else None

    @classmethod
    def disconnect_user(cls, username: str):
        """Disconnect a user by username (for bans/kicks)"""
        for api_key, info in list(cls.connected_clients.items()):
            if info.get("client_name") == username:
                websocket = info["websocket"]  # type: WebSocket
                try:
                    asyncio.create_task(websocket.close(code=1008, reason="Disconnected by admin"))
                except:
                    pass
                cls.remove(websocket)
                print(f"🚫 Disconnected user: {username}")
                break
    
    @classmethod
    def cleanup_inactive(cls, timeout=60):
        now = time.time()
        to_remove = [api_key for api_key, info in cls.connected_clients.items() if now - info["last_seen"].timestamp() > timeout]
        for api_key in to_remove:
            cls.remove(cls.connected_clients[api_key]["websocket"])
            asyncio.create_task(cls.connected_clients[api_key]["websocket"].close())  # Graceful disconnect