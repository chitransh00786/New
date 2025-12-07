"""
Client action management (ban, mute, kick) using JSON file storage
"""
import json
import os
from typing import List, Dict, Optional
from datetime import datetime

CLIENT_ACTIONS_FILE = "json/client_actions.json"

class ClientActionsManager:
    """Manage client actions (ban, mute, kick) using JSON file"""
    
    @staticmethod
    def _ensure_file_exists():
        """Ensure client actions file exists"""
        if not os.path.exists(CLIENT_ACTIONS_FILE):
            os.makedirs(os.path.dirname(CLIENT_ACTIONS_FILE), exist_ok=True)
            with open(CLIENT_ACTIONS_FILE, 'w') as f:
                json.dump({"actions": []}, f, indent=4)
    
    @staticmethod
    def _load_data() -> Dict:
        """Load client actions data"""
        ClientActionsManager._ensure_file_exists()
        try:
            with open(CLIENT_ACTIONS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading client actions: {e}")
            return {"actions": []}
    
    @staticmethod
    def _save_data(data: Dict):
        """Save client actions data"""
        try:
            with open(CLIENT_ACTIONS_FILE, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving client actions: {e}")
    
    @staticmethod
    def ban_client(username: str, admin_username: str, reason: str = None, expires_at: str = None) -> bool:
        """Ban a client"""
        data = ClientActionsManager._load_data()
        
        # Deactivate any previous bans
        for action in data["actions"]:
            if action["username"] == username and action["action_type"] == "ban" and action["is_active"]:
                action["is_active"] = False
        
        action = {
            "id": len(data["actions"]) + 1,
            "username": username,
            "action_type": "ban",
            "reason": reason or "",
            "admin_username": admin_username,
            "expires_at": expires_at,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat()
        }
        
        data["actions"].append(action)
        ClientActionsManager._save_data(data)
        return True
    
    @staticmethod
    def unban_client(username: str, admin_username: str) -> bool:
        """Unban a client"""
        data = ClientActionsManager._load_data()
        
        # Deactivate active bans
        updated = False
        for action in data["actions"]:
            if action["username"] == username and action["action_type"] == "ban" and action["is_active"]:
                action["is_active"] = False
                updated = True
        
        # Record unban action
        action = {
            "id": len(data["actions"]) + 1,
            "username": username,
            "action_type": "unban",
            "reason": "",
            "admin_username": admin_username,
            "expires_at": None,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat()
        }
        
        data["actions"].append(action)
        ClientActionsManager._save_data(data)
        return True
    
    @staticmethod
    def mute_client(username: str, admin_username: str, reason: str = None, expires_at: str = None) -> bool:
        """Mute a client"""
        data = ClientActionsManager._load_data()
        
        # Deactivate previous mutes
        for action in data["actions"]:
            if action["username"] == username and action["action_type"] == "mute" and action["is_active"]:
                action["is_active"] = False
        
        action = {
            "id": len(data["actions"]) + 1,
            "username": username,
            "action_type": "mute",
            "reason": reason or "",
            "admin_username": admin_username,
            "expires_at": expires_at,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat()
        }
        
        data["actions"].append(action)
        ClientActionsManager._save_data(data)
        return True
    
    @staticmethod
    def kick_client(username: str, admin_username: str, reason: str = None) -> bool:
        """Kick a client"""
        data = ClientActionsManager._load_data()
        
        action = {
            "id": len(data["actions"]) + 1,
            "username": username,
            "action_type": "kick",
            "reason": reason or "",
            "admin_username": admin_username,
            "expires_at": None,
            "is_active": True,
            "created_at": datetime.utcnow().isoformat()
        }
        
        data["actions"].append(action)
        ClientActionsManager._save_data(data)
        return True
    
    @staticmethod
    def is_banned(username: str) -> bool:
        """Check if a client is currently banned"""
        data = ClientActionsManager._load_data()
        
        for action in data["actions"]:
            if action["username"] == username and action["action_type"] == "ban" and action["is_active"]:
                # Check if ban expired
                if action.get("expires_at"):
                    try:
                        expires = datetime.fromisoformat(action["expires_at"])
                        if expires < datetime.utcnow():
                            action["is_active"] = False
                            ClientActionsManager._save_data(data)
                            return False
                    except:
                        pass
                return True
        
        return False
    
    @staticmethod
    def is_muted(username: str) -> bool:
        """Check if a client is currently muted"""
        data = ClientActionsManager._load_data()
        
        for action in data["actions"]:
            if action["username"] == username and action["action_type"] == "mute" and action["is_active"]:
                # Check if mute expired
                if action.get("expires_at"):
                    try:
                        expires = datetime.fromisoformat(action["expires_at"])
                        if expires < datetime.utcnow():
                            action["is_active"] = False
                            ClientActionsManager._save_data(data)
                            return False
                    except:
                        pass
                return True
        
        return False
    
    @staticmethod
    def get_banned_clients() -> List[Dict]:
        """Get all currently banned clients"""
        data = ClientActionsManager._load_data()
        
        banned = []
        for action in data["actions"]:
            if action["action_type"] == "ban" and action["is_active"]:
                banned.append({
                    "id": action["id"],
                    "username": action["username"],
                    "reason": action["reason"],
                    "admin": action["admin_username"],
                    "created_at": action["created_at"],
                    "expires_at": action.get("expires_at")
                })
        
        return banned
    
    @staticmethod
    def get_all_actions(limit: int = 100) -> List[Dict]:
        """Get all client actions"""
        data = ClientActionsManager._load_data()
        
        # Sort by created_at descending and limit
        actions = sorted(data["actions"], key=lambda x: x["created_at"], reverse=True)[:limit]
        
        return [{
            "id": action["id"],
            "username": action["username"],
            "action": action["action_type"],
            "reason": action["reason"],
            "admin": action["admin_username"],
            "is_active": action["is_active"],
            "created_at": action["created_at"],
            "expires_at": action.get("expires_at")
        } for action in actions]
