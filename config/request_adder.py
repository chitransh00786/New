import json
import logging
from datetime import datetime
import spotipy, time
from spotipy.oauth2 import SpotifyClientCredentials
from spotipy.exceptions import SpotifyException
from requests.exceptions import ReadTimeout
from config.config import Authorization
from config.songHandler import songHandler
from config.blocker import blocker
from config.requestHandler import requestHandler

logger = logging.getLogger(__name__)

class RequestAdder:
    def __init__(self):
        self.APPS_FILE = "json/apps.json"
        self.sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=Authorization.SPOTIPY_CLIENT_ID, 
            client_secret=Authorization.SPOTIPY_CLIENT_SECRET,
            requests_timeout=30  # Increase timeout to 30 seconds
        ))
        self.songHandler = songHandler
        self.requestHandler = requestHandler
        self.blocker = blocker

    def load_json_data(self, file_location):
        """Load authorized keys from the JSON file."""
        try:
            with open(file_location, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    # Function to save data to a JSON file
    def save_json(self, file_location, data):
        with open(file_location, "w") as file:
            json.dump(data, file, indent=4)
 

    def get_app_id(self, app_name):
        """
        Map app name to app ID using the apps dictionary.
        If the app name is not found, return None.
        """
        apps = self.load_json_data(self.APPS_FILE)
        
        for app_id, name in apps.items():
            if name.lower() == app_name.lower():
                return app_id
        return None

    def get_song_data(self, song_name: str, requester: str, id: str) -> dict:
        max_retries = 3
        delay = 2  # seconds

        for attempt in range(max_retries):
            try:
                results = self.sp.search(q=song_name, limit=1, type='track')

                if not results['tracks']['items']:
                    return {"error": "Track not found"}

                track = results['tracks']['items'][0]

                data = {
                    "title": track['name'],
                    "artist": ', '.join(artist['name'] for artist in track['artists']),
                    "album": track['album']['name'],
                    "played": datetime.now().isoformat(),
                    "duration": track['duration_ms'],
                    "albumart": track['album']['images'][0]['url'] if track['album']['images'] else None,
                    "YEAR": track['album']['release_date'][:4],
                    "spotifyID": track['id'],
                    "requester": requester,
                    "apprequest": id,
                    "radioname": "Pew Hits",
                    "radionameshort": "Pew",
                    "external_url": track['external_urls']['spotify']
                }

                return data

            except (ReadTimeout, SpotifyException) as e:
                print(f"Attempt {attempt+1} failed with error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(delay * (attempt + 1))  # backoff delay
                else:
                    return {"error": f"Spotify API error after {max_retries} attempts: {e}"}
            except Exception as e:
                return {"error": f"Unexpected error: {e}"}
        
    def get_song_data_by_id(self, track_id: str, requester: str, id: str) -> dict:
        max_retries = 3
        delay = 2  # seconds

        for attempt in range(max_retries):
            try:
                track = self.sp.track(track_id)

                if not track:
                    return {"error": "Track not found"}

                data = {
                    "title": track['name'],
                    "artist": ', '.join(artist['name'] for artist in track['artists']),
                    "album": track['album']['name'],
                    "played": datetime.now().isoformat(),
                    "duration": track['duration_ms'],
                    "albumart": track['album']['images'][0]['url'] if track['album']['images'] else None,
                    "YEAR": track['album']['release_date'][:4],
                    "spotifyID": track['id'],
                    "requester": requester,
                    "apprequest": id,
                    "radioname": "Pew Hits",
                    "radionameshort": "Pew",
                    "external_url": track['external_urls']['spotify'],
                    "popularity": track.get('popularity', 0),
                    "preview_url": track.get('preview_url')
                }

                return data

            except (ReadTimeout, SpotifyException) as e:
                print(f"Attempt {attempt+1} failed with error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(delay * (attempt + 1))  # exponential backoff
                else:
                    return {"error": f"Spotify API error after {max_retries} attempts: {e}"}

            except Exception as e:
                return {"error": f"Unexpected error: {e}"}

    async def request_maker(self, song_id: str, requester: str, app: str, youtube_url: str = "", 
                        title: str = None, artist: str = None, album: str = None, 
                        duration: int = None, albumart: str = None):
        """
        Add a song request to the queue.

        :param song_id: ID of the song to search for (or synthetic ID for YouTube-only)
        :param requester: The name or ID of the person requesting the song
        :param app: The application requesting the song
        :param youtube_url: Optional YouTube URL for manual downloads
        :param title: Optional title (for YouTube-only requests)
        :param artist: Optional artist (for YouTube-only requests)
        :param album: Optional album (for YouTube-only requests)
        :param duration: Optional duration in seconds (for YouTube-only requests)
        :param albumart: Optional album art URL (for YouTube-only requests)
        :return: A message indicating the success or failure of the request
        """
        try:
            logger.info(f"🎯 request_maker called for song_id: {song_id}, requester: {requester}, youtube_url: {youtube_url or 'None'}")
            
            # Map app name to app ID
            app_id = self.get_app_id(app) if app else None

            # Determine apprequest value
            if app and app_id is None:
                logger.warning(f"Unknown app: {app}")
                return f"Unknown app: {app}"
            
            # Check if this is a YouTube-only request (metadata provided, no Spotify lookup needed)
            is_youtube_only = song_id.startswith("youtube_") and title and artist
            
            if is_youtube_only:
                # Create song_data from provided metadata (skip Spotify lookup)
                logger.info(f"🎥 YouTube-only request detected, using provided metadata")
                song_data = {
                    "title": title,
                    "artist": artist,
                    "album": album or "YouTube",
                    "played": datetime.now().isoformat(),
                    "duration": duration * 1000 if duration else 0,  # Convert to ms
                    "albumart": albumart or None,
                    "YEAR": datetime.now().year,
                    "spotifyID": song_id,  # Use synthetic ID
                    "requester": requester,
                    "apprequest": str(app_id),
                    "radioname": "Pew Hits",
                    "radionameshort": "Pew",
                    "external_url": youtube_url,
                    "youtube_url": youtube_url
                }
            else:
                # Get song data from Spotify (normal flow)
                song_data = self.get_song_data_by_id(song_id, requester, str(app_id))
                
                if "error" in song_data:
                    logger.warning(f"Spotify error: {song_data['error']}")
                    return song_data["error"]
                
                # Add YouTube URL to song_data if provided
                if youtube_url:
                    song_data['youtube_url'] = youtube_url
                    logger.info(f"🔗 YouTube URL attached: {youtube_url}")
            
            logger.info(f"✅ Got song data: {song_data.get('title')} - {song_data.get('artist')}")

            if self.songHandler.track_already_played(song_data):
                logger.info("❌ Check failed: track_already_played")
                return "error 609: Song recently played."

            if not is_youtube_only and self.blocker.is_song_blocked(song_data['spotifyID']):
                logger.info("❌ Check failed: is_song_blocked")
                return "error 709: Requested song is blocked."

            if self.requestHandler.check_request_exists(song_data['spotifyID']):
                logger.info("❌ Check failed: check_request_exists")
                return "error 708: You have already requested this song, and it is waiting in the request queue to be played."

            if self.songHandler.next_coming_file_exists(song_data['spotifyID']):
                logger.info("❌ Check failed: next_coming_file_exists")
                return "error 707: This song is already in the queue to be played."

            if self.songHandler.now_playing_file_exists(song_data['spotifyID']):
                logger.info("❌ Check failed: now_playing_file_exists")
                return "error 706: This song is currently playing."
            
            logger.info("✅ All checks passed, calling add_request()")
            # Add the request to the queue
            self.requestHandler.add_request(song_data)
            logger.info(f"✅ Request added successfully for: {song_data.get('title')}")
            return song_data
        except Exception as e:
            logger.error(f"❌ Exception in request_maker: {e}", exc_info=True)
            print(f"An error occurred: {e}")

requestAdder = RequestAdder()