class Authorization:
    ngrok_auth_token = "2ryTNIRJt7EfI8YH911S9pw3SGN_2bXVyi1ZdVMHAWgF6vJ6d"
    ngrok_domain = "stirring-thoroughly-kid.ngrok-free.app"
    SPOTIPY_CLIENT_ID = '61f786cf566f4e2489c4f0507a3e059e'
    SPOTIPY_CLIENT_SECRET = '02786c3a37cf4e6f8fe56f5d5a73aae3'
    
class DJ:
    playlists = [
        "https://open.spotify.com/playlist/3GPazbIsrzEFersZ1mDnjS?si=PwcHsHm5SAeCJDUU4SsQ1A"
    ]
    
class Server:
    IP = "154.43.62.60"
    PORT = 9364
    CLIENT_BASE_URL = f"http://{IP}:{PORT}"
    
class config:
    # --- CONFIGURATION ---
    SERVER_HOST = "80.225.211.98"       # your Icecast server IP or domain
    SERVER_PORT = 8261                  # Icecast port (usually 8000)
    ADMIN = "1LoVVe"                    # Icecast Admin username
    ADMIN_PASSWORD = "Study@hard819DJ"  # Icecast Admin password
    MOUNT_POINT = "/pewhitsdesi"        # mount point (e.g., /radio.mp3)
    STREAM_PASSWORD = "Study@hard819"   # Icecast source password
    BITRATE = "128k"                    # bitrate for ffmpeg output
    AUDIO_FILE = "Nothing.mp3"          # path to silent audio file
    RECONNECT_DELAY = 5                 # seconds between reconnection attempts
    RADIO_NAME = "Pew Hits"
    RADIO_DESC = "The best tunes, 24/7 live!"
    GENRE = "Ambient"
    GEMINI_API_KEY = "AIzaSyCFR6LQmpq5DwZ6uWGOrcEjVLgmbca5sW8"
    
    SENDER_EMAIL = "chitransh819@gmail.com"
    APP_PASSWORD = "dkio tsbp broz cppr"  # use App Password (not your normal Gmail password!)