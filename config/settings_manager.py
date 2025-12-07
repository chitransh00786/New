"""
Settings management using JSON file storage
"""
import json
import os
from typing import Dict, List, Optional
from datetime import datetime

SETTINGS_FILE = "json/settings.json"

class SettingsManager:
    """Manage system settings using JSON file"""
    
    @staticmethod
    def _ensure_file_exists():
        """Ensure settings.json exists with default settings"""
        if not os.path.exists(SETTINGS_FILE):
            default_settings = {
                "endpoints": {
                    "api_search": {"enabled": True, "display_name": "Song Search", "description": "Enable/disable Spotify song search"},
                    "api_play": {"enabled": True, "display_name": "Play Request", "description": "Enable/disable song requests"},
                    "api_play_youtube": {"enabled": True, "display_name": "YouTube Play", "description": "Enable/disable YouTube direct requests"},
                    "api_skip": {"enabled": True, "display_name": "Skip Song", "description": "Enable/disable song skip feature"},
                    "api_queue_remove": {"enabled": True, "display_name": "Queue Remove", "description": "Enable/disable removing songs from queue"},
                    "api_queue_move_top": {"enabled": True, "display_name": "Move to Top", "description": "Enable/disable moving songs to queue top"},
                    "api_playlists": {"enabled": True, "display_name": "Playlist Management", "description": "Enable/disable playlist management"},
                    "api_now_playing": {"enabled": True, "display_name": "Now Playing", "description": "Enable/disable now playing endpoint"},
                    "api_next_coming": {"enabled": True, "display_name": "Next Coming", "description": "Enable/disable next coming endpoint"},
                    "api_queue": {"enabled": True, "display_name": "Queue List", "description": "Enable/disable queue list endpoint"},
                    "api_recommendations": {"enabled": True, "display_name": "Recommendations", "description": "Enable/disable song recommendations"}
                },
                "broadcasts": {
                    "ws_now_playing": {"enabled": True, "display_name": "Now Playing Broadcast", "description": "Enable/disable live now playing updates"},
                    "ws_next_coming": {"enabled": True, "display_name": "Next Coming Broadcast", "description": "Enable/disable next song updates"},
                    "ws_queue": {"enabled": True, "display_name": "Queue Broadcast", "description": "Enable/disable queue updates"},
                    "ws_playlists": {"enabled": True, "display_name": "Playlist Broadcast", "description": "Enable/disable playlist updates"}
                },
                "features": {
                    "feature_requests": {"enabled": True, "display_name": "Song Requests", "description": "Enable/disable all song request features"},
                    "feature_skip": {"enabled": True, "display_name": "Skip Feature", "description": "Enable/disable skip functionality"},
                    "feature_youtube": {"enabled": True, "display_name": "YouTube Support", "description": "Enable/disable YouTube direct requests"}
                },
                "last_updated": datetime.utcnow().isoformat()
            }
            
            os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(default_settings, f, indent=4)
            
            print(f"✅ Created default settings file: {SETTINGS_FILE}")
    
    @staticmethod
    def _load_settings() -> Dict:
        """Load settings from JSON file"""
        SettingsManager._ensure_file_exists()
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading settings: {e}")
            return {}
    
    @staticmethod
    def _save_settings(settings: Dict):
        """Save settings to JSON file"""
        try:
            settings["last_updated"] = datetime.utcnow().isoformat()
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")
    
    @staticmethod
    def is_enabled(setting_name: str) -> bool:
        """Check if a setting is enabled"""
        settings = SettingsManager._load_settings()
        
        # Check in all categories
        for category in ['endpoints', 'broadcasts', 'features']:
            if category in settings and setting_name in settings[category]:
                return settings[category][setting_name].get('enabled', True)
        
        return True  # Default to enabled if not found
    
    @staticmethod
    def toggle_setting(setting_name: str, enabled: bool) -> bool:
        """Toggle a setting on/off"""
        settings = SettingsManager._load_settings()
        
        # Find and update in all categories
        for category in ['endpoints', 'broadcasts', 'features']:
            if category in settings and setting_name in settings[category]:
                settings[category][setting_name]['enabled'] = enabled
                SettingsManager._save_settings(settings)
                return True
        
        return False
    
    @staticmethod
    def get_all_settings() -> List[Dict]:
        """Get all settings grouped by type"""
        settings = SettingsManager._load_settings()
        result = []
        
        for category, items in settings.items():
            if category == "last_updated":
                continue
            
            for name, config in items.items():
                result.append({
                    "name": name,
                    "display_name": config.get("display_name", name),
                    "type": category.rstrip('s'),  # endpoint, broadcast, feature
                    "enabled": config.get("enabled", True),
                    "description": config.get("description", "")
                })
        
        return result
    
    @staticmethod
    def get_category_settings(category: str) -> Dict:
        """Get settings for a specific category"""
        settings = SettingsManager._load_settings()
        return settings.get(category, {})
