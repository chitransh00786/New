import asyncio
import random
import time
import datetime
import logging
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials
from google import genai
from config.config import config, Authorization

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
logger = logging.getLogger("HybridDJ")


class AiSelector:
    def __init__(self):
        self.GEMINI_API_KEY = config.GEMINI_API_KEY
        self.GEMINI_MODEL = None  # Will be set upon client initialization
        self.gemini_client_async = self.initialize_gemini_client()
        self.sp = self.create_spotify_client()
        
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
        
    def initialize_gemini_client(self):
        """
        Initializes the Gemini client if the API key is available.
        """
        try:
            if self.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY" or not self.GEMINI_API_KEY:
                raise ValueError("GEMINI_API_KEY is not set. Please set it or replace the placeholder.")
            self.gemini_client_async = genai.Client(api_key=self.GEMINI_API_KEY).aio
            self.GEMINI_MODEL = 'gemini-2.5-flash-live'
            logger.info("🤖 Gemini Client Initialized.")
        except Exception as e:
            logger.error(f"🚨 Failed to initialize Gemini Client: {e}")
            # Fallback to a simpler client/mock if necessary, but we'll halt if key is missing.
            return None
        
        return self.gemini_client_async
    
    def get_current_location_and_time(self):
        """Mock function to get real-world context data."""
        # NOTE: In a production environment, you would use an IP geolocation service
        # and a reliable time API (like timezonedb) to get accurate, real-time data.
        return {
            'location': "Mumbai, India",
            'datetime_str': datetime.datetime.now().strftime("%I:%M %p, %A, %B %d, %Y")
        }
    
    
    async def hybrid_select_next(self, history):
        """
        The AI suggests the next song, and Spotify finds the exact track ID.
        """
        # 1. AI Reasoning for the NEXT track
        ai_suggestion = await self.ai_select_seed(history)
        
        if ai_suggestion:
            # 2. Look up the AI's suggested track on Spotify to get the actual ID/metadata
            # The spotify_search_track function returns the final, best track dict
            next_song = await asyncio.to_thread(self.spotify_search_track, ai_suggestion['title'], ai_suggestion['artist'])
            
            if next_song:
                # 3. Simple Check: Ensure it hasn't been played in the last 10 songs
                new_track_id = next_song.get('track_id') or next_song.get('id')
                # Use .get() defensively in history list to handle tracks missing an ID key
                history_ids = {t.get("id") or t.get("track_id") for t in history if t.get("id") or t.get("track_id")}
                if new_track_id in history_ids:
                    logger.warning(f"⚠️ AI suggested song {next_song['title']} is a recent repeat. Falling back to history seed.")
                else:
                    logger.info(f"✅ FINAL PICK: {next_song['title']} — {next_song['artist']}")
                    return next_song
            
        # 4. Fallback: If AI failed, lookup failed, or it was a repeat, use a random song 
        # from the initial set of tracks as a temporary seed for an old-school selection
        logger.warning("No valid AI-suggested song generated/found. Returning None.")
        return None
    
    async def ai_select_seed(self, history_track_data):
        """
        Uses Gemini to suggest seed tracks for Spotify's recommendation engine.
        Returns: a suggested track dictionary {'title': str, 'artist': str} or None.
        """
        
        context = self.get_current_location_and_time()
        current_location = context['location']
        current_time_date = context['datetime_str']
        
        # 1. Build the prompt text based on the last 5 played songs
        prompt_lines = [
            # --- PRIMARY CHANGE: LANGUAGE AND GENRE ---
            "You are an expert music curator named Pew Hits, specializing in **contemporary English-language music (Pop, Rock, Hip-Hop, Dance)**. Your goal is to select **ONE English song** that perfectly matches the current real-world context and maintains the flow of the recent playlist.",
            "",
            # --- ENVIRONMENTAL CONTEXT REMAINS THE SAME ---
            "**Current Environmental Context (ANALYZE THIS):**",
            f"* **Current Location:** {current_location}",
            f"* **Current Time & Date:** {current_time_date}",
            "The track must be highly relevant to the **Time of Day Vibe**, the local **Weather Mood**, and any globally/locally relevant **Holiday/Festival Mood**.",
            "",
            # --- PLAYLIST CONTEXT REMAINS THE SAME ---
            "**Recent Playlist Context (MAINTAIN THIS VIBE):**",
            "Recent songs (used to maintain genre/vibe continuity):"
        ]
        
        context_tracks = history_track_data[-5:]  # Last 5 tracks for context
        
        for idx, t in enumerate(context_tracks, start=1):
            # We need to handle the case where 'mood' might not be present in the newly fetched tracks
            mood = t.get('mood', 'No Mood Tag')
            prompt_lines.append(f"{idx}. {t['title']} - {t['artist']} ({mood})")
            
        prompt_lines.append(
            "\nSuggest ONE next song that best synthesizes ALL contextual factors (Time, Weather, Holiday, and Recent Vibe)."
            "Return **only** the song name and artist, separated by a hyphen, like 'Song Name - Artist'."
            "DO NOT include any extra text, numbers, or bullet points."
        )
        prompt = "\n".join(prompt_lines)
        
        if not self.gemini_client_async:
            logger.warning("🤖 Gemini Client not available. Using simple random fallback.")
            return None # Indicate fallback is needed elsewhere
            
        try:
            # 2. Call the Gemini API
            response = await self.gemini_client_async.models.generate_content(
                model=self.GEMINI_MODEL, 
                contents=prompt
            )
            
            # 3. Parse the output
            raw_response = response.text.strip()
            logger.info(f"🤖 Gemini Raw Response: {raw_response}")
            
            if ' - ' in raw_response:
                parts = raw_response.split(' - ', 1)
                suggested_track = {
                    'title': parts[0].strip(),
                    'artist': parts[1].strip()
                }
                logger.info(f"🤖 Parsed AI Suggestion: {suggested_track['title']} by {suggested_track['artist']}")
                return suggested_track
            else:
                logger.warning("🤖 AI response was unparsable, falling back.")
                return None

        except Exception as e:
            logger.error(f"🚨 Error calling Gemini API: {e}")
            return None
        
    def spotify_search_track(self, title: str, artist: str):
        """
        Search Spotify for a specific track by title and artist.
        
        Returns a dictionary with keys:
            'title', 'artists', 'album', 'track_id', 'duration_sec'
        """
        q = f"track:{title} artist:{artist}"
        try:
            # Assuming 'downloader.sp' is your initialized Spotify client
            results = self.sp.search(q=q, limit=1, type='track')
            tracks = results['tracks']['items']
            
            if tracks:
                t = tracks[0]
                
                # Extracting Artist names into a list of strings
                artists_list = [a["name"] for a in t["artists"]]
                
                # Convert milliseconds to seconds
                duration_sec = t["duration_ms"] // 1000
                
                logger.info(f"🔍 Found Spotify ID for AI suggestion: {t['name']} by {artists_list[0]}")
                
                return {
                    # --- FINAL, STANDARDIZED KEYS (Used by the entire system) ---
                    "track_id": t["id"],
                    "title": t["name"],
                    "artist": ", ".join(artists_list),
                    "album": t["album"]["name"],
                    "duration_sec": duration_sec,
                    # --- KEY REQUIRED BY songHandler.save_to_next_coming ---
                    "track": t, # The raw Spotify object containing all details
                    # --- Other required keys ---
                    "query": f"{t['name']} {', '.join(artists_list)}",
                    "url": t["external_urls"]["spotify"],
                }
            
            logger.warning(f"🔍 Spotify search failed to find: {title} by {artist}")
            return None
            
        except Exception as e:
            logger.error(f"⚠️ Spotify search error: {e}")
            return None
        
aiselector = AiSelector()