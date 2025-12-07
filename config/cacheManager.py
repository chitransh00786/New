import os
import shutil
import time
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)

class CacheManager:
    """Manages song cache with size limits and cleanup"""
    
    def __init__(self, cache_dir: str = "Cache", max_cache_size_mb: int = 500):
        self.cache_dir = cache_dir
        self.max_cache_size = max_cache_size_mb * 1024 * 1024  # Convert to bytes
        self.ensure_cache_directory()
    
    def ensure_cache_directory(self):
        """Create cache directory if it doesn't exist"""
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
            logger.info(f"Created cache directory at {self.cache_dir}")
    
    def sanitize_filename(self, title: str) -> str:
        """Remove invalid characters from filename"""
        invalid_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
        sanitized = title
        for char in invalid_chars:
            sanitized = sanitized.replace(char, '')
        return sanitized
    
    def get_cached_path(self, title: str) -> str:
        """Get the full path for a cached file"""
        safe_title = self.sanitize_filename(title)
        return os.path.join(self.cache_dir, f"{safe_title}.mp3")
    
    def is_cached(self, title: str) -> bool:
        """Check if a song exists in cache"""
        cached_path = self.get_cached_path(title)
        exists = os.path.exists(cached_path)
        if exists:
            logger.info(f"Found {title} in cache")
            # Update access time
            os.utime(cached_path, None)
        return exists
    
    def get_from_cache(self, title: str) -> Optional[str]:
        """Get cached file path if it exists"""
        if self.is_cached(title):
            cached_path = self.get_cached_path(title)
            logger.info(f"Using cached version of {title} from {cached_path}")
            return cached_path
        logger.info(f"{title} not found in cache")
        return None
    
    def add_to_cache(self, source_path: str, title: str) -> bool:
        """Copy a file to cache"""
        try:
            self.ensure_cache_directory()
            cached_path = self.get_cached_path(title)
            
            logger.info(f"Attempting to cache {source_path} as {title}")
            
            if os.path.exists(source_path):
                shutil.copy2(source_path, cached_path)
                logger.info(f"Successfully cached file at {cached_path}")
                self.cleanup_if_needed()
                return True
            
            logger.warning(f"Source file not found at {source_path}")
            return False
        except Exception as e:
            logger.error(f"Error caching file: {e}")
            return False
    
    def cleanup_if_needed(self):
        """Remove old files if cache exceeds size limit"""
        try:
            self.ensure_cache_directory()
            
            # Get all files with their stats
            files = []
            for filename in os.listdir(self.cache_dir):
                filepath = os.path.join(self.cache_dir, filename)
                if os.path.isfile(filepath):
                    stat = os.stat(filepath)
                    files.append({
                        'path': filepath,
                        'size': stat.st_size,
                        'atime': stat.st_atime
                    })
            
            if not files:
                return
            
            # Sort by access time (oldest first)
            files.sort(key=lambda x: x['atime'])
            
            total_size = sum(f['size'] for f in files)
            logger.info(f"Current cache size: {total_size / 1024 / 1024:.2f}MB")
            
            # Remove oldest files until under limit
            while total_size > self.max_cache_size and files:
                oldest = files.pop(0)
                try:
                    os.remove(oldest['path'])
                    total_size -= oldest['size']
                    logger.info(f"Removed {os.path.basename(oldest['path'])} from cache due to size limit")
                except Exception as e:
                    logger.error(f"Failed to remove {oldest['path']}: {e}")
        
        except Exception as e:
            logger.error(f"Error cleaning cache: {e}")
    
    def get_cache_size(self) -> int:
        """Get total cache size in bytes"""
        total = 0
        for filename in os.listdir(self.cache_dir):
            filepath = os.path.join(self.cache_dir, filename)
            if os.path.isfile(filepath):
                total += os.path.getsize(filepath)
        return total
    
    def get_random_from_cache(self) -> Optional[str]:
        """Get a random song from cache as fallback"""
        import random
        try:
            self.ensure_cache_directory()
            files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir) if f.endswith('.mp3')]
            if files:
                return random.choice(files)
            return None
        except Exception as e:
            logger.error(f"Error getting random cache file: {e}")
            return None


# Global cache manager instance
cache_manager = CacheManager()
