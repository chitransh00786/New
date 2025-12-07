import os
import asyncio
import logging
from typing import Optional, Dict
from config.cacheManager import cache_manager
from config.downloaders.soundcloud_downloader import SoundCloudDownloader
from config.downloaders.jiosaavn_downloader import JioSaavnDownloader
import yt_dlp
import subprocess

logger = logging.getLogger(__name__)

# Suppress yt-dlp verbose logging
logging.getLogger('yt_dlp').setLevel(logging.ERROR)
logging.getLogger('yt-dlp').setLevel(logging.ERROR)

class UnifiedDownloader:
    """
    Downloads songs using a fallback chain:
    1. Check Cache
    2. Try SoundCloud
    3. Try JioSaavn
    4. Fallback to YouTube
    """
    
    def __init__(self):
        self.soundcloud = SoundCloudDownloader()
        self.jiosaavn = JioSaavnDownloader()
        self.download_dir = "Downloads"
        os.makedirs(self.download_dir, exist_ok=True)
    
    async def download_from_url(self, url: str, title: str, output_dir: str = None) -> Optional[str]:
        """Download audio from a direct URL"""
        if output_dir is None:
            output_dir = self.download_dir
        
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{cache_manager.sanitize_filename(title)}.mp3")
        
        try:
            logger.info(f"Downloading {title} from URL")
            
            # Download with ffmpeg
            cmd = [
                'ffmpeg', '-y', '-i', url,
                '-c:a', 'libmp3lame',
                '-b:a', '192k',
                output_path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            await process.communicate()
            
            if process.returncode == 0 and os.path.exists(output_path):
                logger.info(f"Successfully downloaded {title}")
                # Add to cache and get cached path
                cached_path = cache_manager.get_cached_path(title)
                if cache_manager.add_to_cache(output_path, title):
                    # Delete the Downloads file after successful caching
                    try:
                        if os.path.exists(output_path) and output_path != cached_path:
                            os.remove(output_path)
                            logger.info(f"🗑️ Cleaned up: {output_path}")
                    except Exception as e:
                        logger.warning(f"Failed to delete download file: {e}")
                return cached_path
            else:
                logger.error(f"FFmpeg failed to download {title}")
                return None
                
        except Exception as e:
            logger.error(f"Error downloading from URL: {e}")
            return None
    
    async def download_from_youtube(self, search_query: str, title: str, expected_duration: int = None, youtube_url: str = None) -> Optional[str]:
        """Download from YouTube using yt-dlp
        
        Args:
            search_query: Search query for YouTube
            title: Song title for output filename
            expected_duration: Expected duration of the song (optional)
            youtube_url: Direct YouTube URL to download (optional, overrides search)
        """
        try:
            if youtube_url:
                logger.info(f"🔗 Using provided YouTube URL: {youtube_url}")
            else:
                logger.info(f"Searching YouTube for: {search_query}")
            
            output_path = os.path.join(self.download_dir, f"{cache_manager.sanitize_filename(title)}.mp3")
            
            COOKIES_FILE = 'cookies.txt'
            BACKUP_COOKIES = 'backup_cookies.txt'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_path.replace('.mp3', '.%(ext)s'),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'no_warnings': True,
                'no_color': True,
                'noprogress': True,
                'socket_timeout': 60,
                'retries': 3,
                'verbose': False,
                'logger': logging.getLogger('null'),  # Discard logs
                'geo_bypass': True,
                'cookiefile': COOKIES_FILE,
                'noprogress': True,
                'http_headers': headers
            }
            
            # Use direct URL if provided, otherwise search
            download_url = youtube_url if youtube_url else f"ytsearch1:{search_query}"
            
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: yt_dlp.YoutubeDL(ydl_opts).download([download_url])
            )
            
            if os.path.exists(output_path):
                logger.info(f"✅ Successfully downloaded from YouTube: {title}")
                # Add to cache and get cached path
                cached_path = cache_manager.get_cached_path(title)
                if cache_manager.add_to_cache(output_path, title):
                    # Delete the Downloads file after successful caching
                    try:
                        if os.path.exists(output_path) and output_path != cached_path:
                            os.remove(output_path)
                            logger.info(f"🗑️ Cleaned up: {output_path}")
                    except Exception as e:
                        logger.warning(f"Failed to delete download file: {e}")
                return cached_path
            else:
                logger.error(f"YouTube download completed but file not found: {title}")
                return None
                
        except Exception as e:
            logger.error(f"Error downloading from YouTube: {e}")
            return None
    
    async def download_song(self, song_name: str, artist_name: str = "", expected_duration: int = None, youtube_url: str = None) -> Optional[Dict[str, any]]:
        """
        Download a song using the fallback chain:
        Cache → SoundCloud → JioSaavn → YouTube
        
        Args:
            song_name: Name of the song
            artist_name: Name of the artist
            expected_duration: Expected duration in seconds
            youtube_url: Optional direct YouTube URL (skips search, used in fallback)
        
        Returns dict with 'path' and 'source' keys
        """
        from mutagen.mp3 import MP3
        title = f"{song_name} - {artist_name}" if artist_name else song_name
        search_query = f"{song_name} {artist_name}" if artist_name else song_name
        
        logger.info(f"Starting download chain for: {title}")
        if youtube_url:
            logger.info(f"🔗 YouTube URL provided for fallback: {youtube_url}")
        
        # 1. Check cache first
        cached_path = cache_manager.get_from_cache(title)
        if cached_path:
            return {
                'path': cached_path,
                'source': 'cache',
                'title': title,
                'duration': (MP3(cached_path)).info.length
            }
        
        # 2. Try JioSaavn
        try:
            logger.info("Trying JioSaavn...")
            js_result = await self.jiosaavn.get_song_by_name(search_query)
            if js_result and js_result.get('url'):
                download_path = await self.download_from_url(js_result['url'], title)
                if download_path:
                    return {
                        'path': download_path,
                        'source': 'jiosaavn',
                        'title': title,
                        'duration': js_result.get('duration')
                    }
        except Exception as e:
            logger.warning(f"JioSaavn download failed: {e}")
            
        # 3. Try SoundCloud
        try:
            logger.info("Trying SoundCloud...")
            sc_result = await self.soundcloud.get_song_by_name(search_query)
            if sc_result and sc_result.get('url'):
                # Use SoundCloud's own download method with yt-dlp
                output_path = os.path.join(self.download_dir, f"{cache_manager.sanitize_filename(title)}.mp3")
                download_path = self.soundcloud.download_from_url(sc_result['url'], output_path)
                if download_path:
                    # Add to cache
                    cached_path = cache_manager.get_cached_path(title)
                    if cache_manager.add_to_cache(download_path, title):
                        # Delete the Downloads file after successful caching
                        try:
                            if os.path.exists(download_path) and download_path != cached_path:
                                os.remove(download_path)
                                logger.info(f"🗑️ Cleaned up: {download_path}")
                        except Exception as e:
                            logger.warning(f"Failed to delete download file: {e}")
                    return {
                        'path': cached_path,
                        'source': 'soundcloud',
                        'title': title,
                        'duration': sc_result.get('duration')
                    }
        except Exception as e:
            logger.warning(f"SoundCloud download failed: {e}")
        
        # 4. Fallback to YouTube (use provided URL if available)
        try:
            logger.info("Falling back to YouTube...")
            yt_path = await self.download_from_youtube(search_query, title, expected_duration, youtube_url=youtube_url)
            if yt_path:
                return {
                    'path': yt_path,
                    'source': 'youtube',
                    'title': title
                }
        except Exception as e:
            logger.error(f"YouTube download failed: {e}")
        
        # 5. Last resort: pick a random song from cache
        logger.warning(f"All download methods failed for: {title}, picking random from cache...")
        try:
            random_cached = cache_manager.get_random_from_cache()
            if random_cached:
                logger.info(f"Using random cached song as fallback: {random_cached}")
                return {
                    'path': random_cached,
                    'source': 'cache_fallback',
                    'title': os.path.basename(random_cached).replace('.mp3', '')
                }
        except Exception as e:
            logger.error(f"Cache fallback also failed: {e}")
        
        logger.error(f"All download methods failed for: {title}")
        return None


# Global downloader instance
unified_downloader = UnifiedDownloader()
