import asyncio, time
from spotipy import Spotify
from spotipy.exceptions import SpotifyException
from requests.exceptions import ReadTimeout
from spotipy.oauth2 import SpotifyClientCredentials
from collections import deque
from datetime import datetime
import os
from pathlib import Path
from dataclasses import asdict
from config.config import Authorization, config
from config.BG_process_status import songDownloader
from config.PlaylistHandler import pl
from config.downloaders.unified_downloader import unified_downloader
from config.songHandler import songHandler
from config.AiSelector import aiselector
from config.blocker import blocker
from config.requestHandler import requestHandler
from Websocket.websocket import broadcast_song

# Global download lock to prevent concurrent downloads
download_lock = asyncio.Lock()

class TaskGroup:
    def __init__(self):
        self._tasks = set()

    def create_task(self, coro):
        """Create and register an asyncio task."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def manage_task(self, task_name: str, coro_func):
        """
        Cancel existing task with the same name, then create a new one.
        Ensures only one instance of that named task runs.
        """
        task_list = list(self._tasks)

        # Cancel any existing task with the same name
        for task in task_list:
            if task.get_name() == task_name:
                print(f"[TASKGROUP] Cancelling existing task: {task_name}")
                task.cancel()

        # Create new task
        new_task = self.create_task(coro_func)
        new_task.set_name(task_name)
        print(f"[TASKGROUP] Started task: {task_name}")
        return new_task


# Create global instance
taskgroup = TaskGroup()

class Downloader:
    def __init__(self):
        self.sp = self.create_spotify_client()
        self.unified_downloader = unified_downloader
        self.songHandler = songHandler
        self.AiSelector = aiselector
        self.pl = pl
        self.requestHandler = requestHandler
        self.blocker = blocker
        self.next_coming_file = self.songHandler.next_coming_file
        self.download_lock = asyncio.Lock()
        self.is_downloading = False
        

        # Internal state mirrors JSONs
        self.now_playing = deque(maxlen=1)
        self.next_coming = deque(maxlen=3)
        self.queue = deque(maxlen=20)
        
        
    def create_spotify_client(self, retries=3, delay=5):
        for attempt in range(retries):
            try:
                return Spotify(auth_manager=SpotifyClientCredentials(
                    client_id=Authorization.SPOTIPY_CLIENT_ID,
                    client_secret=Authorization.SPOTIPY_CLIENT_SECRET,
                    requests_timeout=30
                ))
            except Exception as e:
                print(f"🚨 Spotify Auth Error (attempt {attempt+1}/{retries}): {e}")
                time.sleep(delay * (attempt + 1))  # exponential backoff
        raise Exception("❌ Failed to authenticate with Spotify after multiple attempts.")
    
    async def song_downloader(self):
        
        songDownloader.song_downloader = True
        await asyncio.sleep(5) # Wait for 5 seconds to start the current song timer
        request = self.requestHandler.get_request()
        if request:
            spotify_id = request.spotifyID
            requester = request.requester
            appreq = request.apprequest
            duration = request.duration // 1000  # convert to seconds
            # Extract youtube_url and metadata if it exists in the request
            youtube_url = getattr(request, 'youtube_url', None)
            # For YouTube-only songs, pass the request metadata directly
            request_metadata = None
            if spotify_id and spotify_id.startswith("youtube_"):
                request_metadata = {
                    'title': request.title,
                    'artist': request.artist,
                    'album': request.album,
                    'albumart': request.albumart
                }
            if spotify_id:
                asyncio.create_task(self.download_song_from_id(spotify_id, requester, appreq, duration, youtube_url, request_metadata))
                self.requestHandler.remove_request(spotify_id)
                
                # Broadcast queue update since song moved to next_coming
                # Use create_task to avoid blocking and defer imports to avoid circular dependency
                async def broadcast_updated_queue():
                    try:
                        from Websocket.websocket import broadcast_queue_update
                        queue = self.requestHandler.get_requests()
                        queue_data = [asdict(song) for song in queue] if queue else []
                        await broadcast_queue_update(queue_data)
                        print("📣 Broadcasted queue update (song moved to next_coming)")
                    except Exception as e:
                        print(f"⚠️ Failed to broadcast queue update: {e}")
                
                asyncio.create_task(broadcast_updated_queue())
        else:
            asyncio.create_task(self.download_song_from_playlist())
            
    async def download_song_from_id(self, track_id, requester, appreq, length, youtube_url=None, request_metadata=None):
        """
        Downloads a song using its Spotify track ID (or YouTube-only synthetic ID).
        Uses UnifiedDownloader: Cache → SoundCloud → JioSaavn → YouTube fallback
        
        Args:
            track_id (str): Spotify track ID or YouTube synthetic ID (youtube_xxx).
            requester (str): User who requested the song.
            length (int): Expected duration in seconds.
            youtube_url (str, optional): Direct YouTube URL for manual downloads.
            request_metadata (dict, optional): Metadata for YouTube-only songs from the request queue.
        
        Returns:
            None
        """
        
        # Acquire download lock to prevent concurrent downloads
        async with download_lock:
            print("🔒 Acquired download lock, starting download...")
            try:
                # Check if this is a YouTube-only request (synthetic ID)
                is_youtube_only = track_id.startswith("youtube_")
                
                if is_youtube_only:
                    # YouTube-only song - use metadata from request queue
                    print(f"🎥 YouTube-only song detected: {track_id}")
                    
                    if request_metadata:
                        # Use metadata from the request queue directly
                        track_name = request_metadata.get('title', 'Unknown Title')
                        artist_name = request_metadata.get('artist', 'Unknown Artist')
                        album_name = request_metadata.get('album', 'YouTube')
                        albumart = request_metadata.get('albumart')
                        print(f"Found YouTube track from request: {track_name} by {artist_name}")
                        
                        # Save metadata to next_coming.json
                        youtube_metadata = {
                            "ID": track_id,
                            "title": track_name,
                            "artist": artist_name,
                            "album": album_name,
                            "played": datetime.now().isoformat(),
                            "albumart": albumart,
                            "release_date": "",
                            "spotifyID": track_id,
                            "requester": requester,
                            "apprequest": appreq,
                            "radioname": config.RADIO_NAME,
                            "durationsec": length,
                            "position": 0,
                            "remaining": length,
                            "external_url": youtube_url
                        }
                        self.songHandler.save_json(self.next_coming_file, [youtube_metadata])
                        await asyncio.sleep(2)
                        nc_data = self.songHandler.get_next_coming_data()
                        if nc_data:
                            print(f"[DEBUG] Next-coming data prepared: {getattr(nc_data, 'title', None)} - {getattr(nc_data, 'artist', None)}")
                            await broadcast_song("next_coming", "notification", nc_data)
                            print("📣 Broadcasted NEXT COMING to clients ✅")
                        else:
                            print("[DEBUG] No next-coming data found — broadcast skipped ❌")
                    else:
                        print(f"⚠️ No metadata found for YouTube song, skipping...")
                        return
                else:
                    # Regular Spotify song - fetch from Spotify API
                    for attempt in range(3):
                        try:
                            track = self.sp.track(track_id)
                            break
                        except ReadTimeout as e:
                            print(f"Attempt {attempt+1}: Spotify timeout or error - {e}")
                            if attempt == 2:
                                raise
                    
                    track_name = track['name']
                    artist_name = ", ".join(artist['name'] for artist in track['artists'])
                    album_name = track['album']['name']
                    self.songHandler.save_to_next_coming(track, requester, appreq)
                    print(f"Found track: {track_name} by {artist_name}")
                    await asyncio.sleep(2)
                    nc_data = self.songHandler.get_next_coming_data()
                    if nc_data:
                        print(f"[DEBUG] Next-coming data prepared: {getattr(nc_data, 'title', None)} - {getattr(nc_data, 'artist', None)}")
                        await broadcast_song("next_coming", "notification", nc_data)
                        print("📣 Broadcasted NEXT COMING to clients ✅")
                    else:
                        print("[DEBUG] No next-coming data found — broadcast skipped ❌")
                
                # Download using unified downloader (Cache → SoundCloud → JioSaavn → YouTube)
                result = await self.unified_downloader.download_song(
                    song_name=track_name,
                    artist_name=artist_name,
                    expected_duration=length,
                    youtube_url=youtube_url
                )
                
                if result and result.get('path'):
                    # Add the downloaded FILE PATH to next_coming
                    adder.add_song(result['path'])
                    duration = result.get('duration', length)
                    print(f"✅ Downloaded from {result['source']}: {result['path']}")
                else:
                    print(f"⚠️ Failed to download {track_name}, skipping...")
                    return
                
                # Update JSON metadata with song duration for the LAST item (just downloaded)
                next_coming_data = self.songHandler.load_json(self.next_coming_file)
                
                if next_coming_data and isinstance(next_coming_data, list) and len(next_coming_data) > 0:
                    # Update the LAST item (the one we just appended)
                    next_coming_data[-1]["duration"] = int(duration * 1000)
                    next_coming_data[-1]["durationsec"] = int(duration)
                    next_coming_data[-1]["remaining"] = int(duration)
                    self.songHandler.save_json(self.next_coming_file, next_coming_data)
                    
            except SpotifyException as e:
                print(f"Spotify API error: {e}")
            except Exception as e:
                print(f"Error downloading the song: {e}")
            finally:
                songDownloader.song_downloader = False
                print("🔓 Released download lock")
    
    async def get_next_song_from_AIplaylist(self):
        """
        Uses the Hybrid AI DJ to select the next song based on history,
        enforcing a 2 AI : 1 Playlist ratio, with fallbacks.
        
        ASSUMES: Both AI (hybrid_select_next) and Playlist (self.pl.next_song)
        return the full, standardized dict containing all keys, including "track".
        """
        
        MAX_AI_SELECTIONS = 2 
        
        # Ensure the counter is initialized (assuming this happens in __init__)
        if not hasattr(self, 'ai_playlist_switch_counter'):
            self.ai_playlist_switch_counter = 0
            
        is_ai_turn = self.ai_playlist_switch_counter < MAX_AI_SELECTIONS
        track = None
        selection_source = "PewDJ"

        """# --- 1. AI DJ SELECTION ATTEMPT ---
        if is_ai_turn:
            try:
                history_tracks = self.songHandler.get_history()
                
                # This call returns the final, complete track dict on success.
                track = await self.AiSelector.hybrid_select_next(history_tracks)
                
                if track:
                    print(f"🤖 AI DJ (Selection {self.ai_playlist_switch_counter + 1}/{MAX_AI_SELECTIONS}) successfully selected the next track.")
                    self.ai_playlist_switch_counter += 1
                else:
                    print("⚠️ AI DJ could not select a fresh track. Falling back to playlist logic.")
                    is_ai_turn = False 
                    
            except Exception as e:
                print(f"🚨 Critical AI DJ error: {e}. Falling back to simple playlist.")
                is_ai_turn = False """

        # --- 2. PLAYLIST SELECTION (Fallback or Scheduled Playlist Slot) ---
        if not is_ai_turn or track is None:
            while True:
                # ASSUMPTION: self.pl.next_song() returns the full standardized dict
                track = self.pl.next_song()
                
                # Use 'track_id' (or 'id') and ensure the dictionary is valid
                track_id = track.get('track_id') or track.get('id')
                
                # Check for invalid playlist return (e.g., {"message": "No tracks available!"})
                if 'message' in track or not track_id:
                    if 'message' in track and "No tracks available" in track['message']:
                        print("🛑 Critical: Playlist is completely empty and could not be rebuilt.")
                        # Consider raising an error or returning a null value that is handled upstream
                        return None, None, None, None, 0 
                    print("⚠️ Playlist track is missing essential data. Skipping.")
                    continue
                
                # Extract basic track details for logging/history checks
                track_name = track.get('title', 'Unknown Title')
                track_artist = track.get('artist', 'Unknown Artist')
                
                # --- BLOCKING AND HISTORY CHECKS ---
                if self.blocker.is_song_blocked(track_id) or self.songHandler.track_already_played_id(track_id):
                    print(f"Track {track_name} by {track_artist} (song) was blocked/recently played. Trying another track...")
                    continue
                
                # If valid, update the counter and break the inner loop
                print(f"🔄 Playlist selected track '{track_name}' (Resetting AI counter).")
                self.ai_playlist_switch_counter = 0 
                break

        # If we reached here, 'track' is guaranteed to be a valid, fully-formed song dict.
        
        # --- 3. FINAL PROCESSING AND BROADCAST ---
        
        # We now access the fully standardized keys directly from 'track',
        # eliminating the redundant self.get_track_details_by_id call.
        track_data = track # Use the fully formatted track object
        
        # Extract keys for final return statement
        track_id = track.get("track_id") or track.get('id')
        track_name = track.get("title")
        track_artist = track.get("artist")
        track_album = track.get('album', 'Unknown Album')
        track_duration_sec = track.get('duration_sec', 180)
        
        # Save the fully formed track_data (guaranteed not to be None)
        self.songHandler.save_to_next_coming(track_data, selection_source)
        await asyncio.sleep(2)
        
        nc_data = self.songHandler.get_next_coming_data()
        if nc_data:
            print(f"[DEBUG] Next-coming data prepared by {selection_source}: {getattr(nc_data, 'title', None)} - {getattr(nc_data, 'artist', None)}")
            # Assuming broadcast_song is globally available or defined elsewhere
            await broadcast_song("next_coming", "notification", nc_data) 
            print("📣 Broadcasted NEXT COMING to clients ✅")
        else:
            print("[DEBUG] No next-coming data found — broadcast skipped ❌")
            
        return track_name, track_artist, track_id, f"{track_name} {track_artist} {track_album}", track_duration_sec

    async def download_song_from_playlist(self):
        """
        Download a song from the playlist using UnifiedDownloader.
        Uses fallback chain: Cache → SoundCloud → JioSaavn → YouTube
        """
        
        # Get next song and save metadata BEFORE acquiring lock (so next_coming is never empty)
        track_name, track_artist, track_id, search_query, track_duration_sec = await self.get_next_song_from_AIplaylist()
        
        # Acquire download lock to prevent concurrent downloads
        async with download_lock:
            print("🔒 Acquired download lock for playlist song...")
            try:
                print(f"⬇️ Downloading: {track_name} by {track_artist} (Track ID: {track_id})")

                # Download using unified downloader (Cache → SoundCloud → JioSaavn → YouTube)
                result = await self.unified_downloader.download_song(
                    song_name=track_name,
                    artist_name=track_artist,
                    expected_duration=track_duration_sec
                )
                
                if result and result.get('path'):
                    # Add the downloaded FILE PATH to next_coming
                    adder.add_song(result['path'])
                    duration = result.get('duration', track_duration_sec)
                    print(f"✅ Downloaded from {result['source']}: {result['path']}")
                else:
                    print(f"⚠️ Failed to download {track_name}, skipping...")
                    return
                    
                # Update JSON metadata with song duration for the LAST item (just downloaded)
                next_coming_data = self.songHandler.load_json(self.next_coming_file)
                
                if next_coming_data and isinstance(next_coming_data, list) and len(next_coming_data) > 0:
                    # Update the LAST item (the one we just appended)
                    next_coming_data[-1]["duration"] = int(duration * 1000)
                    next_coming_data[-1]["durationsec"] = int(duration)
                    next_coming_data[-1]["remaining"] = int(duration)
                    self.songHandler.save_json(self.next_coming_file, next_coming_data)
                
            except Exception as e:
                print(f"Error downloading playlist song: {e}")
            finally:
                songDownloader.song_downloader = False
                print("🔓 Released download lock")
                
                
class SongAdder:
    def __init__(self, playlist_path="playlist.txt"):
        self.playlist_path = Path(playlist_path)
        self.playlist_path.touch(exist_ok=True)

    def add_song(self, file_path: str) -> bool:
        """Add a song to playlist.txt if it exists and is not already queued."""
        file_path = os.path.abspath(file_path)

        if not os.path.exists(file_path):
            print(f"[ADDER] ❌ File not found: {file_path}")
            return False

        # Read existing playlist
        with open(self.playlist_path, "r", encoding="utf-8") as f:
            existing = [line.strip() for line in f if line.strip()]

        # Avoid duplicates
        if file_path in existing:
            print(f"[ADDER] ⚠️ Song already in queue: {file_path}")
            return False

        # Append new song
        with open(self.playlist_path, "a", encoding="utf-8") as f:
            f.write(file_path + "\n")

        print(f"[ADDER] ✅ Added to playlist: {file_path}")
        return True
                
adder = SongAdder()
downloader = Downloader()