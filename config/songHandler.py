import json, asyncio, os
from datetime import datetime
from typing import Optional
from config.config import config
from Websocket.models import NowPlayingSong, NextComingSong


class SongHandler:
    def __init__(self):
        self.now_playing_file = "json/now_playing.json"
        self.next_coming_file = "json/next_coming.json"
        self.history_file = "json/history.json"

    # Function to load data from a JSON file
    def load_json(self, file_location):
        try:
            if os.path.getsize(file_location) == 0:  # Check if file is empty
                return {}  # Return empty dict instead of crashing
            with open(file_location, "r") as file:
                return json.load(file)
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            return {}

    # Function to save data to a JSON file
    def save_json(self, file_location, data):
        safe_data = self.make_json_safe(data)
        with open(file_location, 'w', encoding='utf-8') as file:
            json.dump(safe_data, file, indent=4)
            
    def make_json_safe(self, obj):
        """Recursively convert sets to lists for JSON serialization."""
        if isinstance(obj, set):
            return list(obj)
        elif isinstance(obj, dict):
            return {k: self.make_json_safe(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.make_json_safe(i) for i in obj]
        else:
            return obj

    # Function to move the data from next_coming.json to now_playing.json
    async def move_to_now_playing(self):
        next_coming_data = self.load_json(self.next_coming_file)
        if next_coming_data:
            # Save the data to now_playing.json
            self.save_json(self.now_playing_file, next_coming_data)
            # Clear the next_coming.json data
            self.save_json(self.next_coming_file, {})
        
    async def update_position_and_remaining(self):
        try:
            data = self.load_json(self.now_playing_file)

            if not data:
                print("[POSITION] ⚠️ now_playing.json is empty. Skipping position update.")
                return

            # Handle both list and dict formats
            if isinstance(data, list):
                item = data[0] if data else None
            elif isinstance(data, dict):
                item = data
            else:
                print(f"[POSITION] ⚠️ Unexpected data type: {type(data)}")
                return

            if not item:
                print("[POSITION] ⚠️ No valid item found in now_playing.json.")
                return

            duration = item.get("durationsec", 0)
            if not duration or duration <= 0:
                print("[POSITION] ⚠️ Invalid or missing duration, skipping update loop.")
                return

            print(f"[POSITION] ⏱️ Starting position tracking for: {item.get('title', 'Unknown Song')}")
            second = 0

            while second < duration - 1:
                item["position"] = second
                item["remaining"] = duration - second

                # Save updated now_playing data
                if isinstance(data, list):
                    data[0] = item
                else:
                    data = item

                self.save_json(self.now_playing_file, data)
                await asyncio.sleep(1)
                second += 1

            print(f"[POSITION] ✅ Completed tracking for: {item.get('title', 'Unknown Song')}")

        except Exception as e:
            print(f"[POSITION] ⚠️ Error updating position: {e}")

    def save_to_next_coming(self, track_data, requester, appreq = None):
        
        # 🛑 CRITICAL VALIDATION STEP: Stop non-dictionary data from crashing the function 🛑
        if not isinstance(track_data, dict):
            print(f"🚨 Critical Data Error: Expected dictionary for track_data, got {type(track_data).__name__}. Aborting save.")
            return 
            
        # 1. Determine if the data is wrapped (AI/Playlist flow) or raw (handler internal)
        if "track" in track_data:
            raw_track = track_data["track"] 
        else:
            raw_track = track_data 
            
        if not isinstance(raw_track, dict):
            # This handles cases where the 'track' key leads to a malformed/non-dict object
            print(f"🚨 Critical Data Error: Raw track data is malformed ({type(raw_track).__name__}). Aborting save.")
            return 

        # 2. Extract Title and Artist using safe retrieval, preferring Spotify's 'name'/'artists'
        title = raw_track.get('name') or raw_track.get('title')
        
        artist_data = raw_track.get('artists')
        if isinstance(artist_data, list):
            artist = ", ".join(a['name'] for a in artist_data)
        else:
            # Fallback for simple string artist (used by playlist flow)
            artist = raw_track.get('artist', 'Unknown Artist')
            

        new_song = {
            "ID": raw_track.get('id') or raw_track.get('track_id'), 
            "title": title, 
            "artist": artist, 
            
            # Safely access nested album keys
            "album": raw_track.get('album', {}).get('name', 'Unknown Album'),
            "played": datetime.now().isoformat(),
            
            # Safely access nested album art and release date
            "albumart": raw_track.get('album', {}).get('images', [{}])[0].get('url') if raw_track.get('album', {}).get('images') else None,
            "release_date": raw_track.get('album', {}).get('release_date', 'N/A'),
            
            "spotifyID": raw_track.get('id') or raw_track.get('track_id'),
            "requester": requester,
            "apprequest": appreq,
            "radioname": config.RADIO_NAME,
            
            # Safely access duration, converting from ms to sec
            "durationsec": raw_track.get('duration_ms', 180000) // 1000,
            "position": 0,
            "remaining": raw_track.get('duration_ms', 180000) // 1000,
            "external_url": raw_track.get('external_urls', {}).get('spotify')
        }
        
        self.save_json(self.next_coming_file, new_song)

    
    # Function to add now-playing data to history.json
    async def add_to_history(self):
        now_playing_data = self.load_json(self.now_playing_file)
        history_data = self.load_json(self.history_file)

        if not now_playing_data:
            print("[HISTORY] ⚠️ now_playing.json is empty — skipping history update.")
            return

        # Handle both dict and list formats
        if isinstance(now_playing_data, list):
            song = now_playing_data[0] if now_playing_data else None
        elif isinstance(now_playing_data, dict):
            song = now_playing_data
        else:
            print("[HISTORY] ❌ Invalid now_playing.json format.")
            return

        if not song or "ID" not in song:
            print("[HISTORY] ⚠️ No valid song data to add to history.")
            return

        song_id = song["ID"]

        # Remove if already exists
        if song_id in history_data:
            del history_data[song_id]

        # Add or update history
        history_data[song_id] = {
            "title": song.get("title", ""),
            "artist": song.get("artist", ""),
            "album": song.get("album", ""),
            "played": song.get("played", ""),
            "durationsec": song.get("durationsec", 0),
            "albumart": song.get("albumart", ""),
            "release_date": song.get("release_date", ""),
            "spotifyID": song.get("spotifyID", ""),
            "external_url": song.get("external_url", "")
        }

        self.save_json(self.history_file, history_data)
        print(f"[HISTORY] ✅ Added '{song.get('title', 'Unknown')}' to history.")
        
    def get_history(self) -> list:
        """
        Loads the entire history, sorts it by 'played' time, and returns 
        the last 10 songs as a list of dictionaries.
        
        The dictionaries are formatted to contain keys expected by ai_select_seed:
        id, title, artist, and a placeholder mood (since the original history 
        doesn't store mood, we include a default).
        """
        raw_history_data = self.load_json(self.history_file)
        
        if not raw_history_data:
            return []
        
        # 1. Convert the dictionary of songs into a list of song objects (dictionaries)
        # The history dictionary keys are the song IDs.
        history_list = list(raw_history_data.values())
        
        if not history_list:
            return []
        
        # 2. Sort the list by the 'played' timestamp (ISO format)
        # The ISO format string can be sorted lexicographically, but using a datetime 
        # object is safer/more robust if the format ever changes slightly.
        try:
            # We sort in ascending order (oldest first)
            history_list.sort(key=lambda song: datetime.fromisoformat(song.get('played', '1970-01-01T00:00:00')))
        except Exception as e:
            # Fallback if played timestamp is missing or malformed
            print(f"[HISTORY] ⚠️ Failed to sort history by 'played' time: {e}")
            return []
            
        # 3. Take the last 10 songs (the most recently played)
        # We also map the keys to match the expected format used in the AI prompt ('mood' required)
        last_three_songs = history_list[-10:]

        # 4. Format the output to match the expected track dict for the AI prompt
        formatted_history = []
        for song in last_three_songs:
            # We need to ensure we have a 'mood' key for the AI prompt
            # Since your history file doesn't store mood, we'll use a placeholder.
            # In a real system, you'd fetch the mood/audio features here.
            
            # NOTE: We use 'spotifyID' as 'id' and include a fallback 'mood'
            formatted_history.append({
                "id": song.get("spotifyID", song.get("ID")),
                "title": song.get("title", "Unknown Title"),
                "artist": song.get("artist", "Unknown Artist"),
                "album": song.get("album", "Unknown Album"),
                # Placeholder for the mood required by the AI prompt
                "mood": "Mixed Genre (Recent History)"
                # In a production app, you would enrich this with audio features!
            })
            
        return formatted_history
        
    def get_now_playing_data(self) -> Optional[NowPlayingSong]:
        now_playing_data = self.load_json(self.now_playing_file)

        if not now_playing_data:
            return None

        # Handle both list or dict structures
        if isinstance(now_playing_data, list):
            if len(now_playing_data) == 0:
                return None
            song = now_playing_data[0]  # take first element if list
        elif isinstance(now_playing_data, dict):
            song = now_playing_data
        else:
            return None

        # Now safely construct the dataclass
        try:
            return NowPlayingSong(
                ID=song.get("ID"),
                title=song.get("title"),
                artist=song.get("artist"),
                album=song.get("album"),
                played=song.get("played"),
                albumart=song.get("albumart"),
                release_date=song.get("release_date"),
                spotifyID=song.get("spotifyID"),
                requester=song.get("requester"),
                apprequest=song.get("apprequest"),
                radioname=song.get("radioname"),
                durationsec=song.get("durationsec"),
                position=song.get("position"),
                remaining=song.get("remaining"),
                external_url=song.get("external_url")
            )
        except Exception as e:
            print(f"[ERROR] Failed to parse now-playing data: {e}")
            return None

    def get_next_coming_data(self) -> Optional[NextComingSong]:
        """Load and return the next-coming song as a dataclass object."""
        next_coming_data = self.load_json(self.next_coming_file)

        if not next_coming_data:
            return None

        # Handle both dict and list cases
        if isinstance(next_coming_data, list):
            if len(next_coming_data) == 0:
                return None
            song = next_coming_data[0]  # pick first element if list
            # ✅ Optional: Auto-fix file to ensure uniform dict structure
            try:
                self.save_json(self.next_coming_file, song)
                print("[AUTO-FIX] next_coming.json converted from list → dict for consistency.")
            except Exception as e:
                print(f"[WARN] Failed to auto-fix next_coming.json: {e}")
        elif isinstance(next_coming_data, dict):
            song = next_coming_data
        else:
            print(f"[WARN] Unexpected next_coming_data type: {type(next_coming_data)}")
            return None

        # Safely construct dataclass instance
        try:
            return NextComingSong(
                ID=song.get("ID"),
                title=song.get("title"),
                artist=song.get("artist"),
                album=song.get("album"),
                played=song.get("played"),
                albumart=song.get("albumart"),
                release_date=song.get("release_date"),
                spotifyID=song.get("spotifyID"),
                requester=song.get("requester"),
                apprequest=song.get("apprequest"),
                radioname=song.get("radioname"),
                durationsec=song.get("durationsec"),
                position=song.get("position"),
                remaining=song.get("remaining"),
                external_url=song.get("external_url")
            )
        except Exception as e:
            print(f"[ERROR] Failed to parse next-coming data: {e}")
            return None
    
    def next_coming_file_exists(self, spotifyID):
        next_coming_data = self.load_json(self.next_coming_file)
        if next_coming_data:
            for item in next_coming_data:
                # Convert stringified JSON to dict if needed
                if isinstance(item, str):
                    try:
                        item = json.loads(item)
                    except json.JSONDecodeError:
                        continue
                if isinstance(item, dict) and item.get('spotifyID') == spotifyID:
                    return True
        return False

    def now_playing_file_exists(self, spotifyID):
        now_playing_data = self.load_json(self.now_playing_file)
        if now_playing_data:
            for item in now_playing_data:
                # Convert stringified JSON to dict if needed
                if isinstance(item, str):
                    try:
                        item = json.loads(item)
                    except json.JSONDecodeError:
                        continue
                if isinstance(item, dict) and item.get('spotifyID') == spotifyID:
                    return True
        return False

    def track_already_played(self, song_data):
        # Load history from the JSON file
        history = self.load_json(self.history_file)
        
        # Get the Spotify ID safely
        spotify_id = song_data.get('spotifyID')  # Use .get() to avoid KeyError

        if not spotify_id:
            return False  # Assume song was not played if the key is missing

        if spotify_id in history:
            played_time = datetime.fromisoformat(history[spotify_id]['played'])
            if (datetime.now() - played_time).total_seconds() < 3 * 3600:  # 3 hours in seconds
                return True

        return False

    def track_already_played_id(self, spotify_id):
        # Load history from the JSON file
        history = self.load_json(self.history_file)

        if not spotify_id:
            return False  # Assume song was not played if the key is missing

        if spotify_id in history:
            played_time = datetime.fromisoformat(history[spotify_id]['played'])
            if (datetime.now() - played_time).total_seconds() < 5 * 3600:  # 5 hours in seconds
                return True

        return False

    def build_now_playing_from_file(self, url: str) -> bool:
        """
        Build now_playing.json from next_coming data or MP3 file metadata.
        Returns True if successful, False otherwise.
        """
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3
        
        try:
            # Try to read from next_coming.json first to get Spotify metadata
            next_coming_data = self.get_next_coming_data()
            
            # Build now_playing.json from next_coming data or MP3 file
            if url and not url.startswith("http") and os.path.exists(url):
                audio = MP3(url, ID3=ID3)
                duration = int(audio.info.length)
                
                # If we have next_coming data with Spotify metadata, use it
                if next_coming_data and next_coming_data.albumart:
                    now_playing_data = [{
                        "ID": next_coming_data.ID,
                        "title": next_coming_data.title,
                        "artist": next_coming_data.artist,
                        "album": next_coming_data.album,
                        "played": next_coming_data.played,
                        "albumart": next_coming_data.albumart,
                        "release_date": next_coming_data.release_date,
                        "spotifyID": next_coming_data.spotifyID,
                        "requester": next_coming_data.requester,
                        "apprequest": next_coming_data.apprequest,
                        "radioname": next_coming_data.radioname,
                        "durationsec": next_coming_data.durationsec,
                        "position": next_coming_data.position,
                        "remaining": next_coming_data.remaining,
                        "external_url": next_coming_data.external_url
                    }]
                else:
                    # Fallback to MP3 metadata if no Spotify data available
                    title = str(audio.get('TIT2', '')) if audio.get('TIT2') else ''
                    artist = str(audio.get('TPE1', '')) if audio.get('TPE1') else ''
                    album = str(audio.get('TALB', '')) if audio.get('TALB') else ''
                    
                    # If no ID3 tags, extract from filename
                    if not title or not artist:
                        filename = os.path.basename(url)
                        song_name = os.path.splitext(filename)[0]
                        
                        if " - " in song_name:
                            parts = song_name.split(" - ", 1)
                            title = parts[0] if not title else title
                            artist = parts[1] if not artist else artist
                        else:
                            title = song_name if not title else title
                            artist = "Unknown Artist" if not artist else artist
                    
                    title = title or "Unknown Title"
                    artist = artist or "Unknown Artist"
                    album = album or "Unknown Album"
                    
                    now_playing_data = [{
                        "ID": os.path.basename(url),
                        "title": title,
                        "artist": artist,
                        "album": album,
                        "played": "",
                        "albumart": "",
                        "release_date": "",
                        "spotifyID": "",
                        "requester": "AutoDJ",
                        "apprequest": "AutoDJ",
                        "radioname": config.RADIO_NAME,
                        "durationsec": duration,
                        "position": 0,
                        "remaining": duration,
                        "external_url": ""
                    }]
                
                # Save to JSON file
                os.makedirs("json", exist_ok=True)
                self.save_json(self.now_playing_file, now_playing_data)
                return True
            
            return False  # URL doesn't meet conditions
        except Exception as e:
            print(f"⚠️ Failed to build now_playing.json: {e}")
            return False
        
songHandler = SongHandler()