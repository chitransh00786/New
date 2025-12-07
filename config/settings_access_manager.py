"""
Settings access request management using JSON file storage
"""
import json
import os
from typing import List, Dict, Optional
from datetime import datetime

SETTINGS_ACCESS_FILE = "json/settings_access_requests.json"

class SettingsAccessManager:
    """Manage DJ settings access requests using JSON file"""
    
    @staticmethod
    def _ensure_file_exists():
        """Ensure settings access file exists"""
        if not os.path.exists(SETTINGS_ACCESS_FILE):
            os.makedirs(os.path.dirname(SETTINGS_ACCESS_FILE), exist_ok=True)
            with open(SETTINGS_ACCESS_FILE, 'w') as f:
                json.dump({"requests": [], "approved_users": []}, f, indent=4)
    
    @staticmethod
    def _load_data() -> Dict:
        """Load settings access data"""
        SettingsAccessManager._ensure_file_exists()
        try:
            with open(SETTINGS_ACCESS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading settings access data: {e}")
            return {"requests": [], "approved_users": []}
    
    @staticmethod
    def _save_data(data: Dict):
        """Save settings access data"""
        try:
            with open(SETTINGS_ACCESS_FILE, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving settings access data: {e}")
    
    @staticmethod
    def request_access(username: str, reason: str = None) -> bool:
        """DJ requests settings access"""
        data = SettingsAccessManager._load_data()
        
        # Check if already has pending request
        for req in data["requests"]:
            if req["username"] == username and req["status"] == "pending":
                return False
        
        # Check if already approved
        if username in data["approved_users"]:
            return False
        
        request = {
            "id": len(data["requests"]) + 1,
            "username": username,
            "reason": reason or "",
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            "admin_response": None,
            "admin_username": None,
            "updated_at": datetime.utcnow().isoformat()
        }
        
        data["requests"].append(request)
        SettingsAccessManager._save_data(data)
        return True
    
    @staticmethod
    def get_pending_requests() -> List[Dict]:
        """Get all pending access requests"""
        data = SettingsAccessManager._load_data()
        return [req for req in data["requests"] if req["status"] == "pending"]
    
    @staticmethod
    def approve_request(request_id: int, admin_username: str, response: str = None) -> bool:
        """Admin approves settings access request"""
        data = SettingsAccessManager._load_data()
        
        for req in data["requests"]:
            if req["id"] == request_id:
                req["status"] = "approved"
                req["admin_username"] = admin_username
                req["admin_response"] = response or ""
                req["updated_at"] = datetime.utcnow().isoformat()
                
                # Add to approved users
                if req["username"] not in data["approved_users"]:
                    data["approved_users"].append(req["username"])
                
                SettingsAccessManager._save_data(data)
                return True
        
        return False
    
    @staticmethod
    def deny_request(request_id: int, admin_username: str, response: str = None) -> bool:
        """Admin denies settings access request"""
        data = SettingsAccessManager._load_data()
        
        for req in data["requests"]:
            if req["id"] == request_id:
                req["status"] = "denied"
                req["admin_username"] = admin_username
                req["admin_response"] = response or ""
                req["updated_at"] = datetime.utcnow().isoformat()
                
                SettingsAccessManager._save_data(data)
                return True
        
        return False
    
    @staticmethod
    def has_access(username: str) -> bool:
        """Check if DJ has approved settings access"""
        data = SettingsAccessManager._load_data()
        return username in data["approved_users"]
