import json
from Websocket.models import BlockedSongs
from typing import List, Union


class Blocker:
    def __init__(self):
        self.file_location = "json/blocked_songs.json"

    def load_songs(self):
        """
        Load all requests from the JSON file. If the file does not exist, return an empty dictionary.
        """
        try:
            with open(self.file_location, "r") as file:
                return json.load(file)
        except FileNotFoundError:
            return {}

    def save_songs(self, requests):
        """
        Save the updated requests to the JSON file.
        """
        with open(self.file_location, "w") as file:
            json.dump(requests, file, indent=4)
        
    def add_song(self, spotify_id=None, youtube_id=None, title = "", artist = "", album = "", blocker=""):
        """
        Add a song to the blocked list.

        Args:
            spotify_id (str): The Spotify ID of the song.
            youtube_id (str): The YouTube ID of the song.
            reason (str): The reason for blocking the song.

        Returns:
            str: Success or error message.
        """
        if not spotify_id and not youtube_id:
            return "error 801: Provide at least one valid song ID (Spotify or YouTube)."

        blocked_songs = self.load_songs()

        # Generate a unique key based on available IDs
        song_key = spotify_id if spotify_id else youtube_id

        if song_key in blocked_songs:
            return "error 802: This song is already blocked."

        blocked_songs[song_key] = {
            "spotify_id": spotify_id,
            "youtube_id": youtube_id,
            "title": title,
            "artist": artist,
            "album": album,
            "blocker": blocker
        }

        self.save_songs(blocked_songs)
        return f"Current song has been blocked successfully."

    def remove_song(self, spotify_id=None, youtube_id=None):
        """
        Remove a song from the blocked list.

        Args:
            spotify_id (str): The Spotify ID of the song.
            youtube_id (str): The YouTube ID of the song.

        Returns:
            str: Success or error message.
        """
        if not spotify_id and not youtube_id:
            return "error 801: Provide at least one valid index, song ID (Spotify or YouTube)."

        blocked_songs = self.load_songs()

        # Determine the key used
        song_key = spotify_id if spotify_id else youtube_id

        if song_key not in blocked_songs:
            return "error 803: This song is not in the blocked list."

        del blocked_songs[song_key]

        self.save_songs(blocked_songs)
        return f"Song {song_key} has been removed from the blocked list."

    def remove_song_by_index(self, index):
        """
        Remove a blocked song by its index from the JSON file.

        Args:
            index (int): The index (ID) of the request to remove.
            
        Returns:
            str: A message indicating the result of the operation.
        """
        
        index -= 1
        
        if index is None or not isinstance(index, int):
            return "error 801: Provide at least one valid index, song ID (Spotify or YouTube)."
        
        # Load the existing songs
        blocked_songs = self.load_songs()

        # Convert dictionary to a list of songs
        song_list = list(blocked_songs.items())  # [(song_id, song_data), ...]

        # Check if index exists
        if index < 0 or index >= len(song_list):
            return f"error 804: Blocked song at index {index} does not exist."

        # Remove the song
        removed_song_id, _ = song_list.pop(index)

        # Rebuild dictionary with updated order
        updated_data = {song_id: song_data for song_id, song_data in song_list}

        # Save the updated list back to JSON
        self.save_songs(updated_data)

        return f"Blocked song at index {index + 1} has been removed successfully."

    def list_blocked_songs(self) -> Union[List[BlockedSongs], str]:
        """
        Retrieve and display all blocked songs.

        Returns:
            list: List of BlockedSongs instances.
            str: If no songs are blocked, returns a message indicating that.
        """
        blocked_songs_data = self.load_songs()  # Assuming load_songs() returns a dictionary similar to the provided JSON
        if blocked_songs_data:
            blocked_songs_list = []
            for song_id, song_data in blocked_songs_data.items():
                # Create BlockedSongs instance from the song data
                blocked_song = BlockedSongs(
                    spotify_id=song_data.get("spotify_id"),
                    youtube_id=song_data.get("youtube_id"),  # This can be None
                    title=song_data.get("title"),
                    artist=song_data.get("artist"),
                    album=song_data.get("album"),
                    blocker=song_data.get("blocker")
                )
                blocked_songs_list.append(blocked_song)
            return blocked_songs_list
        else:
            return "No songs are currently blocked."

    def is_song_blocked(self, song_id):
        """
        Check if a song is blocked.

        Args:
            song_id (str): The Spotify ID or YouTube ID of the song.

        Returns:
            bool: True if the song is blocked, False otherwise.
        """
        blocked_songs = self.load_songs()
        return song_id in blocked_songs
    
blocker = Blocker()