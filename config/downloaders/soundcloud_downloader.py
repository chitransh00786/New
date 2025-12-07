import os
import re
import logging
import requests
from typing import Optional, Dict
from fuzzywuzzy import fuzz

logger = logging.getLogger(__name__)

# Suppress yt-dlp verbose logging
logging.getLogger('yt_dlp').setLevel(logging.ERROR)
logging.getLogger('yt-dlp').setLevel(logging.ERROR)

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    logger.warning("yt-dlp not available. SoundCloud downloads disabled.")
    YTDLP_AVAILABLE = False

class SoundCloudDownloader:
    """Download songs from SoundCloud using yt-dlp with SoundCloud search"""
    
    def __init__(self):
        self.enabled = YTDLP_AVAILABLE
    
    def _sanitize_filename(self, name: str) -> str:
        """Remove invalid characters from filename"""
        return re.sub(r'[<>:"/\\|?*]', '', name).strip()
        
    def search_soundcloud(self, query: str, threshold: int = 60) -> Optional[Dict]:
        """Search SoundCloud using yt-dlp's search feature, with corrected filtering."""
        if not YTDLP_AVAILABLE:
            logger.debug("yt-dlp not available, skipping SoundCloud...")
            return None
            
        # Define the terms to actively reject
        REJECT_TERMS = ['cover', 'remix', 'slowed', 'reverb', 'bass boosted', 'karaoke', 'instrumental', 'edit', 'live']
        
        try:
            logger.info(f"Searching SoundCloud for: {query}")
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'default_search': 'scsearch10',
                'verbose': False,
                'logger': logging.getLogger('null'),
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_results = ydl.extract_info(f"scsearch10:{query}", download=False)
                
                # ... (Handle no results remains the same) ...
                
                entries = search_results['entries']
                
                best_match = None
                best_score = 0
                query_lower = query.lower()
                
                # --- NEW: Filter and Select Best Match ---
                
                # 🎯 Adjust the threshold for stricter matching
                # Recommended new threshold for Token Sort Ratio: 75-85
                STRICT_THRESHOLD = 80
                
                for entry in entries:
                    if not entry:
                        continue
                        
                    title = entry.get('title', '')
                    title_lower = title.lower()
                    
                    # 1. 🚫 NEGATIVE FILTER CHECK
                    is_rejected = False
                    for term in REJECT_TERMS:
                        # Check if the reject term is in the title...
                        if term in title_lower:
                            # ...AND if that term was NOT in the original query.
                            if term not in query_lower:
                                logger.debug(f"Skipping unsolicited term '{term}' in title: {title}")
                                is_rejected = True
                                break
                    
                    if is_rejected:
                        continue
                                            
                    # 2. 🔑 FUZZY MATCHING (Using fuzz.token_sort_ratio for better accuracy)
                    # This ignores differences in word order (e.g., 'Artist - Title' vs 'Title ft Artist')
                    score = fuzz.token_sort_ratio(query_lower, title_lower)
                    
                    # 3. Preference for better scorea
                    if score > best_score:
                        best_score = score
                        best_match = entry
                        
                # --- End of Filtering Loop ---
                
                # 4. 🛑 Enforce the strict threshold
                if not best_match or best_score < STRICT_THRESHOLD:
                    logger.info(f"No sufficiently similar match found (best: {best_score}%, Required: {STRICT_THRESHOLD}%)")
                    return None
                
                # ... (Existing duration checks and return logic remain the same) ...
                
                duration_sec = best_match.get('duration', 0)
                title = best_match.get('title', 'Unknown Title')
                
                # 🚫 Skip obvious preview clips (less than 60s)
                if duration_sec < 60:
                    logger.warning(f"❌ Rejected {title} — too short ({duration_sec}s, likely preview)")
                    return None
                    
                # Check duration (max 10 minutes)
                if duration_sec > 600:
                    logger.warning(f"SoundCloud track too long: {duration_sec}s")
                    return None
                
                logger.info(f"🎵 Found on SoundCloud: {best_match.get('title')} (Score: {best_score}%, Duration: {duration_sec}s)")
                
                return {
                    'title': best_match.get('title'),
                    'url': best_match.get('url') or best_match.get('webpage_url'),
                    'duration': int(duration_sec),
                    'source': 'soundcloud'
                }
                
        except Exception as e:
            logger.error(f"Error searching SoundCloud: {e}")
            return None
    
    def download_from_url(self, url: str, output_path: str) -> Optional[str]:
        """Download track from SoundCloud URL using yt-dlp"""
        if not YTDLP_AVAILABLE:
            return None
            
        try:
            logger.info(f"🌐 Downloading from SoundCloud: {url}")
            
            # Ensure output directory exists
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            
            # Use absolute path and remove .mp3 extension (yt-dlp adds it)
            abs_output_path = os.path.abspath(output_path)
            output_template = abs_output_path.replace('.mp3', '')
            
            # Configure yt-dlp to download from SoundCloud
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_template,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'keepvideo': False,  # Delete video file after audio extraction
                'verbose': False,
                'noprogress': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                
            final_path = (
                abs_output_path
                if os.path.exists(abs_output_path)
                else output_template + '.mp3'
                if os.path.exists(output_template + '.mp3')
                else None
            )

            if not final_path:
                logger.error("Download completed but file not found")
                return None

            # 🧹 Verify integrity: file size check
            file_size_kb = os.path.getsize(final_path) / 1024
            if file_size_kb < 1000:
                logger.warning(
                    f"⚠️ File too small ({file_size_kb:.1f} kB) — likely truncated preview. Deleting..."
                )
                os.remove(final_path)
                return None

            logger.info(f"✅ Download complete: {final_path} ({file_size_kb:.1f} kB)")
            return final_path
                
        except Exception as e:
            logger.error(f"❌ SoundCloud download failed: {e}")
            return None
    
    async def get_song_by_name(self, song_name: str) -> Optional[Dict[str, any]]:
        """Search for a song on SoundCloud and return download info"""
        if not YTDLP_AVAILABLE:
            logger.debug("yt-dlp not available, skipping SoundCloud...")
            return None
        
        try:
            # Search for the track
            track_info = self.search_soundcloud(song_name)
            
            if not track_info:
                logger.info(f"No good match found on SoundCloud for: {song_name}")
                return None
            
            logger.info(f"Found on SoundCloud: {track_info['title']} ({track_info['duration']}s)")
            return track_info
            
        except Exception as e:
            logger.error(f"Error searching SoundCloud: {e}")
            return None
