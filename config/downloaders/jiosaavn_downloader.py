import requests
import logging
from typing import Optional, Dict
import base64
from Crypto.Cipher import DES
from Crypto.Util.Padding import unpad

logger = logging.getLogger(__name__)

class JioSaavnDownloader:
    """Download songs from JioSaavn"""
    
    BASE_URL = "https://www.jiosaavn.com/api.php"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.REJECT_TERMS = ['cover', 'remix', 'slowed', 'reverb', 'bass boosted', 'karaoke', 'instrumental', 'edit', 'live']
    
    def check_similarity(self, str1: str, str2: str) -> float:
        """Calculate similarity percentage between two strings"""
        str1 = str1.lower()
        str2 = str2.lower()
        
        words1 = set(str1.split())
        words2 = set(str2.split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return (len(intersection) / len(union)) * 100 if union else 0.0
    
    def decrypt_url(self, encrypted_url: str) -> str:
        """Decrypt JioSaavn encrypted media URL"""
        try:
            # JioSaavn uses DES encryption with a known key
            key = b"38346591"
            encrypted_data = base64.b64decode(encrypted_url)
            cipher = DES.new(key, DES.MODE_ECB)
            decrypted = unpad(cipher.decrypt(encrypted_data), DES.block_size)
            return decrypted.decode('utf-8')
        except Exception as e:
            logger.error(f"Error decrypting JioSaavn URL: {e}")
            return encrypted_url
    
    def get_download_links(self, encrypted_url: str) -> list:
        """Generate download links for different qualities"""
        decrypted = self.decrypt_url(encrypted_url)
        qualities = ['_12.mp4', '_48.mp4', '_96.mp4', '_160.mp4', '_320.mp4']
        links = []
        
        for quality in qualities:
            url = decrypted.replace('_96.mp4', quality)
            bitrate = quality.replace('_', '').replace('.mp4', '')
            links.append({
                'quality': f"{bitrate}kbps",
                'url': url
            })
        
        return links
    
    async def get_song_by_name(self, song_name: str) -> Optional[Dict[str, any]]:
        """Search for a song on JioSaavn by name, with filtering."""
        try:
            logger.info(f"Searching JioSaavn for: {song_name}")
            query_lower = song_name.lower() # Prepare query for filtering
            
            params = {
                '__call': 'autocomplete.get',
                'query': song_name,
                'ctx': 'web6dot0',
                '_format': 'json',
                '_marker': '0'
            }
            
            response = self.session.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'songs' not in data or 'data' not in data['songs']:
                logger.info(f"No songs found on JioSaavn for: {song_name}")
                return None
            
            songs = data['songs']['data']
            
            if not songs:
                return None
            
            # --- FILTERING LOGIC ---
            filtered_songs = []
            for song in songs:
                title = song.get('title', '')
                title_lower = title.lower()
                
                is_rejected = False
                for term in self.REJECT_TERMS:
                    # Check if the title contains a reject term
                    if term in title_lower:
                        # AND if that term was NOT explicitly requested in the query
                        if term not in query_lower:
                            logger.debug(f"Skipping unsolicited term '{term}' in JioSaavn title: {title}")
                            is_rejected = True
                            break
                
                if not is_rejected:
                    filtered_songs.append(song)
            
            # Use the filtered list for matching
            songs = filtered_songs
            
            if not songs:
                logger.info(f"All search results filtered out for: {song_name}")
                return None

            # --- FIND BEST MATCH (on filtered list) ---
            best_match = None
            best_score = 0
            
            for song in songs:
                title = song.get('title', '')
                similarity = self.check_similarity(song_name, title) # Uses set intersection for similarity
                
                if similarity > best_score and similarity > 60:
                    best_score = similarity
                    best_match = song
            
            if not best_match:
                logger.info(f"No good match found on JioSaavn for: {song_name}")
                return None
            
            # --- Existing Logic (Fetch Details and Return) ---
            # ... (rest of the function for fetching details remains the same)
            
            # The rest of your function from getting the song_id down to the return
            song_id = best_match.get('id')
            if not song_id:
                return None
            
            # Fetch full song details
            detail_params = {
                '__call': 'song.getDetails',
                'cc': 'in',
                'pids': song_id,
                '_format': 'json',
                '_marker': '0'
            }
            
            detail_response = self.session.get(self.BASE_URL, params=detail_params, timeout=10)
            detail_response.raise_for_status()
            song_data = detail_response.json()
            
            if not song_data or song_id not in song_data:
                return None
            
            song_info = song_data[song_id]
            
            # Check duration
            duration = int(song_info.get('duration', 0))
            if duration > 600:  # 10 minutes
                logger.warning(f"JioSaavn track too long: {duration}s")
                return None
            
            # Get encrypted media URL
            encrypted_url = song_info.get('encrypted_media_url')
            if not encrypted_url:
                return None
            
            # Get highest quality download link (320kbps)
            download_links = self.get_download_links(encrypted_url)
            best_quality = download_links[-1] if download_links else None
            
            if not best_quality:
                return None
            
            logger.info(f"Found on JioSaavn: {song_info.get('title')} ({duration}s)")
            
            return {
                'title': song_info.get('title'),
                'url': best_quality['url'],
                'duration': duration,
                'source': 'jiosaavn'
            }
            
        except requests.exceptions.Timeout:
            logger.error("JioSaavn API timeout")
            return None
        except Exception as e:
            logger.error(f"Error searching JioSaavn: {e}")
            return None
    