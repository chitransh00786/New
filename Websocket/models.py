from dataclasses import dataclass
from typing import Optional
from typing import Union, List

@dataclass
class Error:
    """An error class for categorizing the error message specifically"""
    action: str
    type: str
    rid: str
    code: int
    message: str
    data: str
    error: str

@dataclass
class SessionMetadata:
    """
    Initial session data to be sent to the client on successful WebSocket connection.

    - client_id: Unique ID of the client.
    - client_name: Human-readable name of the client.
    - rate_limits: Dictionary mapping rate limit names (e.g., 'global') to a tuple (limit, interval_in_seconds).
    - connection_id: Unique UUID for this session.
    - sdk_version: Optional version string if client SDK versioning is used.
    """
    
    client_id: str
    client_name: str
    rate_limits: dict[str, tuple[int, float]]
    connection_id: str
    
@dataclass
class ClientInfo:
    """
    Information about the client connected to the WebSocket.
    """
    client_id: str
    client_name: str
    api_key: str
    username: str 
    is_DJ: bool = False
    role: str = "user" 
    client_description: Optional[str] = None
    
@dataclass
class NowPlayingSong:
    """
    Represents the currently playing song.

    - song_id: Unique ID for the song.
    - song_name: Name of the song.
    - artist_name: Name of the artist/band performing the song.
    - album_name: Name of the album the song is part of.
    - duration: Duration of the song in seconds.
    - progress: Current playback progress of the song in seconds (optional, can be None if not tracking).
    """
    ID: str
    title: str
    artist: str
    album: str
    played: str
    albumart: str
    release_date: str
    spotifyID: str
    requester: str
    apprequest: Optional[str]
    radioname: str
    durationsec: int
    position: int
    remaining: int
    external_url: str
    
@dataclass
class NextComingSong:
    """
    Represents the next upcoming song based on the provided JSON data.

    - ID: Unique Spotify ID for the song.
    - title: Title of the song.
    - artist: Name of the artist.
    - album: Name of the album the song is part of.
    - played: The time the song will be played.
    - duration: Duration of the song in milliseconds.
    - albumart: URL for the album art image.
    - YEAR: The year of the album release.
    - spotifyID: Unique Spotify ID (same as 'ID').
    - requester: The ID of the user who requested the song.
    - apprequest: Optional field for application request data (if any).
    - radioname: The name of the radio playing the song.
    - radionameshort: A short name for the radio.
    - durationsec: Duration of the song in seconds.
    - position: The position of the song in the playlist.
    - remaining: Remaining time of the song in seconds.
    - external_url: External URL to the song (e.g., on Spotify).
    """
    ID: str
    title: str
    artist: str
    album: str
    played: str
    albumart: str
    release_date: str
    spotifyID: str
    requester: str
    apprequest: Optional[str]
    radioname: str
    durationsec: int
    position: int
    remaining: int
    external_url: str
    
@dataclass
class QueueSong:
    """
    Represents a song in the queue.

    - id: Unique ID for the song in the queue.
    - title: Title of the song.
    - artist: Name of the artist.
    - album: Name of the album the song is part of.
    - played: The time the song was or will be played.
    - duration: Duration of the song in milliseconds.
    - albumart: URL for the album art image.
    - YEAR: The year of the album release.
    - spotifyID: Unique Spotify ID for the song.
    - requester: The ID of the user who requested the song.
    - apprequest: Optional field for application request data (if any).
    - radioname: The name of the radio playing the song.
    - radionameshort: A short name for the radio.
    - external_url: External URL to the song (e.g., on Spotify).
    """
    id: int
    title: str
    artist: str
    album: str
    played: str
    duration: int
    albumart: str
    YEAR: str
    spotifyID: str
    requester: str
    apprequest: Optional[str]
    radioname: str
    radionameshort: str
    external_url: str
    youtube_url: Optional[str] = None  # Optional field for manual YouTube URL
    
@dataclass
class BlockedSongs:
    """
    Represents a blocked song.

    - spotify_id: Unique Spotify ID for the song.
    - youtube_id: Unique YouTube ID for the song (can be None).
    - title: Title of the song.
    - artist: Name of the artist.
    - album: Name of the album the song is part of.
    - blocker: The ID of the user who blocked the song.
    """
    spotify_id: str
    title: str
    artist: str
    album: str
    blocker: str
    youtube_id: Optional[str] = None  # Optional field goes at the end
    
    
@dataclass
class NowPlayingResponse:
    """The successful response to a `NowPlayingRequest`."""
    action: str
    type: str
    rid: str
    code: int
    message: str
    data: NowPlayingSong
    
@dataclass
class NextComingResponse:
    """The successful response to a `NextComingRequest`."""
    action: str
    type: str
    rid: str
    code: int
    message: str
    data: NextComingSong
    
@dataclass
class QueueResponse:
    """The successful response to a `QueueRequest`."""
    action: str
    type: str
    rid: str
    code: int
    message: str
    data: list[QueueSong]
    
@dataclass
class BlocklistResponse:
    """The successful response to a `BlocklistRequest`."""
    action: str
    type: str
    rid: str
    code: int
    message: str
    data: Union[List[BlockedSongs], str]
    
@dataclass
class KeepaliveResponse:
    """
    A response to a successful reaction.
    """
    action: str
    status: str
    
@dataclass
class SkipSongResponse:
    """
    Represents a response to a skip song request.

    - rid: Unique ID for the request.
    """
    action: str
    type: str
    rid: str
    code: int
    message: str
    data: Union[str, dict]
    
@dataclass
class UnblockSongResponse:
    """
    The successful response to a `UnblockSongRequest`.
    """
    action: str
    type: str
    rid: str
    code: int
    message: str
    data: str
    
@dataclass
class BlockSongResponse:
    """
    The successful response to a `BlockSongRequest`.
    """
    action: str
    type: str
    rid: str
    code: int
    message: str
    data: str
    
@dataclass
class RemoveSongResponse:
    """
    The successful response to a `RemoveSongRequest`.
    """
    action: str
    type: str
    rid: str
    code: int
    message: str
    data: str
    
@dataclass
class PlaySongResponse:
    """
    The successful response to a `PlaySongRequest`.
    """
    action: str
    type: str
    rid: str
    data: str
    code: int
    message: str
    
@dataclass
class ReloadAllResponse:
    """
    The successful response to a `ReloadAllRequest`.
    """
    action: str
    type: str
    rid: str
    data: str
    code: int
    message: str