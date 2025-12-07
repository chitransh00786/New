import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class PromotionManager:
    """Manages promotional audio files with scheduled playback"""
    
    def __init__(self, promotions_dir: str = "promotions", metadata_file: str = "json/promotions.json"):
        self.promotions_dir = promotions_dir
        self.metadata_file = metadata_file
        self.songs_since_last_promo = 0
        self.promo_interval = 23  # Play promo after 20-25 songs (default 23)
        
        # Ensure directories exist
        os.makedirs(self.promotions_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.metadata_file), exist_ok=True)
        
        # Initialize metadata file if it doesn't exist
        if not os.path.exists(self.metadata_file):
            self._save_metadata([])
    
    def _load_metadata(self) -> List[Dict]:
        """Load promotions metadata from JSON file"""
        try:
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []
    
    def _save_metadata(self, promotions: List[Dict]):
        """Save promotions metadata to JSON file"""
        with open(self.metadata_file, 'w', encoding='utf-8') as f:
            json.dump(promotions, f, indent=4, ensure_ascii=False)
    
    def add_promotion(self, name: str, description: str, promoter: str, 
                     from_datetime: str, to_datetime: str, audio_path: str) -> Dict:
        """
        Add a new promotion to the system
        
        Args:
            name: Promotion name
            description: Promotion description
            promoter: Name of the promoter
            from_datetime: Start date/time in ISO format
            to_datetime: End date/time in ISO format
            audio_path: Path to the uploaded MP3 file
        
        Returns:
            Dict with promotion metadata
        """
        promotions = self._load_metadata()
        
        # Generate unique ID
        promo_id = f"promo_{len(promotions) + 1}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Create recognizable filename
        safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in name)
        safe_name = safe_name.replace(' ', '_')
        new_filename = f"{promo_id}_{safe_name}.mp3"
        new_path = os.path.join(self.promotions_dir, new_filename)
        
        # Move/rename the uploaded file
        os.rename(audio_path, new_path)
        
        # Create metadata entry
        promo_metadata = {
            "id": promo_id,
            "name": name,
            "description": description,
            "promoter": promoter,
            "from_datetime": from_datetime,
            "to_datetime": to_datetime,
            "audio_path": new_path,
            "filename": new_filename,
            "created_at": datetime.now().isoformat(),
            "play_count": 0,
            "last_played": None
        }
        
        promotions.append(promo_metadata)
        self._save_metadata(promotions)
        
        logger.info(f"✅ Added promotion: {name} (ID: {promo_id})")
        return promo_metadata
    
    def get_active_promotions(self) -> List[Dict]:
        """Get all currently active promotions based on date/time"""
        promotions = self._load_metadata()
        now = datetime.now()
        
        active_promos = []
        for promo in promotions:
            try:
                from_dt = datetime.fromisoformat(promo['from_datetime']).replace(tzinfo=None)
                to_dt = datetime.fromisoformat(promo['to_datetime']).replace(tzinfo=None)
                
                if from_dt <= now <= to_dt:
                    # Verify audio file still exists
                    if os.path.exists(promo['audio_path']):
                        active_promos.append(promo)
                    else:
                        logger.warning(f"Promo audio file missing: {promo['audio_path']}")
            except (ValueError, KeyError) as e:
                logger.error(f"Invalid promo datetime format: {e}")
        
        return active_promos
    
    def get_all_promotions(self) -> List[Dict]:
        """Get all promotions (active and inactive)"""
        return self._load_metadata()
    
    def should_play_promo(self) -> bool:
        """Check if it's time to play a promo (after 20-25 songs)"""
        return self.songs_since_last_promo >= self.promo_interval
    
    def get_next_promo(self) -> Optional[str]:
        """
        Get the next promo to play. Returns audio file path.
        If multiple promos are active, they play in series.
        """
        active_promos = self.get_active_promotions()
        
        if not active_promos:
            return None
        
        # Sort by play count (least played first) and last_played time
        active_promos.sort(key=lambda p: (p.get('play_count', 0), p.get('last_played', '')))
        
        next_promo = active_promos[0]
        
        # Update play statistics
        self._update_play_stats(next_promo['id'])
        
        logger.info(f"🎤 Playing promo: {next_promo['name']} (by {next_promo['promoter']})")
        return next_promo['audio_path']
    
    def _update_play_stats(self, promo_id: str):
        """Update play count and last played time for a promo"""
        promotions = self._load_metadata()
        
        for promo in promotions:
            if promo['id'] == promo_id:
                promo['play_count'] = promo.get('play_count', 0) + 1
                promo['last_played'] = datetime.now().isoformat()
                break
        
        self._save_metadata(promotions)
    
    def increment_song_count(self):
        """Increment the counter for songs played since last promo"""
        self.songs_since_last_promo += 1
    
    def reset_song_count(self):
        """Reset the counter after playing a promo"""
        self.songs_since_last_promo = 0
    
    def cleanup_expired_promotions(self):
        """Remove expired promotions and their audio files"""
        promotions = self._load_metadata()
        now = datetime.now()
        
        active_promotions = []
        deleted_count = 0
        
        for promo in promotions:
            try:
                to_dt = datetime.fromisoformat(promo['to_datetime']).replace(tzinfo=None)
                
                if now > to_dt:
                    # Promo has expired, delete audio file
                    if os.path.exists(promo['audio_path']):
                        os.remove(promo['audio_path'])
                        logger.info(f"🗑️ Deleted expired promo: {promo['name']} (ID: {promo['id']})")
                    deleted_count += 1
                else:
                    # Keep active/future promotions
                    active_promotions.append(promo)
            except (ValueError, KeyError) as e:
                logger.error(f"Error processing promo for cleanup: {e}")
                active_promotions.append(promo)  # Keep it to avoid data loss
        
        if deleted_count > 0:
            self._save_metadata(active_promotions)
            logger.info(f"✅ Cleaned up {deleted_count} expired promotion(s)")
        
        return deleted_count
    
    def delete_promotion(self, promo_id: str) -> bool:
        """Manually delete a promotion (admin action)"""
        promotions = self._load_metadata()
        
        for i, promo in enumerate(promotions):
            if promo['id'] == promo_id:
                # Delete audio file
                if os.path.exists(promo['audio_path']):
                    os.remove(promo['audio_path'])
                
                # Remove from metadata
                promotions.pop(i)
                self._save_metadata(promotions)
                
                logger.info(f"🗑️ Manually deleted promo: {promo['name']} (ID: {promo_id})")
                return True
        
        return False
    
    def get_promotion_by_id(self, promo_id: str) -> Optional[Dict]:
        """Get a specific promotion by ID"""
        promotions = self._load_metadata()
        
        for promo in promotions:
            if promo['id'] == promo_id:
                return promo
        
        return None
