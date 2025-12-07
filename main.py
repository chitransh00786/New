import asyncio
import socket
import base64
import os
import gc
import psutil
import traceback
import requests
import aiohttp
from dataclasses import asdict
from pathlib import Path
from config.config import config
from config.PromotionManager import PromotionManager
from config.songHandler import SongHandler
from config.requestHandler import RequestHandler
from Websocket.websocket import broadcast_song, broadcast_queue_update, broadcast_listeners_update
from config.DJ import Downloader, taskgroup
from config.state import now_playing, next_coming, song_queue
from Websocket.websocket import start_ws_server_async
from config.PlaylistHandler import SpotifyPlaylistFetcher


class StreamManager:
    def __init__(self):
        # Deques for state management
        
        self.DJ = Downloader()
        self.promo_manager = PromotionManager()
        self.songHandler = SongHandler()
        self.requestHandler = RequestHandler()

        # JSON persistence paths
        self.paths = {
            "now_playing": "json/now_playing.json",
            "next_coming": "json/next_coming.json",
            "queue": "json/queue.json",
        }

        # Configurations
        self.config = config
        self.writer = None  # Icecast connection
        self.reader = None
        self.connected = False
        
        self.playlist_path = Path("playlist.txt")
        self.silence_file = Path(config.AUDIO_FILE)  # Fallback silence audio
        
    def update_icecast_metadata(self, song: str):
        host=f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/"
        mount=config.MOUNT_POINT
        user=config.ADMIN
        password=config.ADMIN_PASSWORD

        url = f"{host}/admin/metadata"
        params = {
            "mount": mount,
            "mode": "updinfo",
            "song": song
        }

        try:
            response = requests.get(url, params=params, auth=(user, password))
            if not response.status_code == 200:
                print(f"⚠️ Failed! Status: {response.status_code}, Response: {response.text}")
        except Exception as e:
            print("🥺 Error updating metadata:", e)
            traceback.print_exc()
            
            
    async def get_next_song(self) -> str | None:
        """Return the next song path (FIFO) from playlist.txt and remove it."""
        if not self.playlist_path.exists():
            await asyncio.sleep(2)
            return None

        try:
            with open(self.playlist_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]

            if not lines:
                return None

            next_song = lines.pop(0)

            # Write remaining lines back (FIFO removal)
            with open(self.playlist_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + ("\n" if lines else ""))

            return next_song if os.path.exists(next_song) else None

        except Exception as e:
            print(f"[FIFO] ⚠️ Error reading playlist: {e}")
            await asyncio.sleep(2)
            return None
        
    async def connect_to_icecast(self, retry_delay: int = 5):
        """
        Establish an asyncio connection to Icecast and send headers.
        Retries automatically on failure until success.
        """
        while True:
            try:
                print(f"[ICECAST] Connecting to {self.config.SERVER_HOST}:{self.config.SERVER_PORT} ...")
                reader, writer = await asyncio.open_connection(
                    self.config.SERVER_HOST, self.config.SERVER_PORT
                )

                auth = f"source:{self.config.STREAM_PASSWORD}"
                headers = (
                    f"PUT {self.config.MOUNT_POINT} HTTP/1.0\r\n"
                    f"Authorization: Basic {base64.b64encode(auth.encode()).decode()}\r\n"
                    f"Content-Type: audio/mpeg\r\n"
                    f"ice-name: {self.config.RADIO_NAME}\r\n"
                    f"ice-description: {self.config.RADIO_DESC}\r\n"
                    f"ice-genre: {self.config.GENRE}\r\n"
                    f"ice-url: http://{self.config.SERVER_HOST}:{self.config.SERVER_PORT}{self.config.MOUNT_POINT}\r\n"
                    f"ice-public: 1\r\n"
                    f"ice-audio-info: bitrate={self.config.BITRATE.replace('k','')}\r\n"
                    f"\r\n"
                )

                writer.write(headers.encode("utf-8"))
                await writer.drain()

                response = await reader.read(1024)
                try:
                    text = response.decode("utf-8", errors="ignore")
                except:
                    text = str(response)

                if "200 OK" in text:
                    print("[ICECAST] ✅ Connected & authenticated successfully.")
                    self.reader = reader
                    self.writer = writer
                    self.connected = True
                    return True
                else:
                    print(f"[ICECAST] ❌ Bad response: {text.strip()}")
                    writer.close()
                    await writer.wait_closed()
            except Exception as e:
                print(f"[ICECAST] ⚠️ Connection error: {e}")

            print(f"[ICECAST] Retrying in {retry_delay}s ...")
            await asyncio.sleep(retry_delay)
            
    async def stream_single_file(self, file_path: str, writer: asyncio.StreamWriter, is_promo: bool = False) -> bool:
        """
        Stream a single MP3 file to Icecast using ffmpeg via asyncio subprocess.
        Keeps the same writer (persistent socket) open across songs.
        Returns:
            True if stream finished cleanly,
            False if writer connection is broken.
        """

        from config.BG_process_status import skip

        if not os.path.exists(file_path):
            print(f"[STREAM] ❌ File not found: {file_path}")
            return True  # skip missing file

        cmd = [
            "ffmpeg", "-re", "-i", file_path,
            "-c:a", "libmp3lame", "-b:a", self.config.BITRATE,
            "-ar", "44100",
            "-f", "mp3", "-"
        ]

        print(f"[STREAM] ▶️ Starting {'promo' if is_promo else 'song'}: {os.path.basename(file_path)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    print(f"[STREAM] ✅ Finished {os.path.basename(file_path)}")
                    break

                # external skip trigger
                if skip.skip_status and not is_promo:
                    print("[STREAM] ⏭️ Skip triggered by user")
                    skip.skip_status = False
                    skip.stop_counter = True
                    break

                try:
                    writer.write(chunk)
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError) as e:
                    print(f"[STREAM] 💥 Writer disconnected: {e}")
                    proc.terminate()
                    await proc.wait()
                    return False  # signal reconnect

                await asyncio.sleep(0)

            if proc.returncode is None:
                proc.terminate()
                await proc.wait()

            return True

        except Exception as e:
            print(f"[STREAM] ⚠️ Error streaming {file_path}: {e}")
            return True
        
    async def stream_audio_loop(self):
        """
        Main loop that coordinates streaming and background downloading.
        """
        
        try:
            await self.songHandler.add_to_history()
            await self.songHandler.move_to_now_playing()

            # Always broadcast next_coming reset
            await broadcast_song("next_coming", "notification", None)
            print("📣 Broadcasted empty next_coming (song changed)")

            # Now_playing broadcast
            np_data = self.songHandler.get_now_playing_data()

            if np_data:
                print(f"[DEBUG] Now-playing data prepared: {np_data.title} - {np_data.artist}")
                await broadcast_song("now_playing", "notification", np_data)
                print("📣 Broadcasted NOW PLAYING to clients ✅")

                # ✅ Safe Icecast update
                song_detail = f"{np_data.title} - {np_data.album}"
            else:
                print("[DEBUG] No now-playing data found — broadcast skipped ❌")
                song_detail = "Silence - Pew Hits"

            self.update_icecast_metadata(song_detail)

            # Queue broadcast
            queue = self.requestHandler.get_requests()
            queue_data = [asdict(song) for song in queue] if queue else []
            await broadcast_queue_update(queue_data)
            print("📣 Broadcasted queue update to clients")
            
            print(f"[STREAM_LOOP] 🎧 Now streaming: {np_data.title}")
            taskgroup.manage_task("update_position", self.songHandler.update_position_and_remaining())

        except Exception as e:
            print(f"[BROADCAST] ⚠️ Error during pre-stream broadcast: {e}")
            traceback.print_exc()

        while True:
            if not self.connected:
                await self.connect_to_icecast()
            
            # === PROMOTION CHECK & PLAYBACK ===
            if self.promo_manager.should_play_promo():
                active_promos = self.promo_manager.get_active_promotions()
                if active_promos:
                    print(f"🎤 Time for promos! {len(active_promos)} active promotion(s)")
                    # Play all active promos in series
                    for promo in active_promos:
                        promo_path = promo['audio_path']
                        if os.path.exists(promo_path):
                            print(f"📢 Playing promo: {promo['name']} by {promo['promoter']}")
                            # Update now_playing to show promo info
                            now_playing.clear()
                            now_playing.append(promo_path)
                            promo_metadata = {
                                "title": promo['name'],
                                "artist": promo['promoter'],
                                "album": promo['description'],
                                "requester": None,
                                "albumart": None,
                                "spotifyID": None
                            }
                            self.songHandler.save_json(self.songHandler.now_playing_file, promo_metadata)
                            
                            # Broadcast promo as now_playing
                            await broadcast_song("now_playing", "notification", promo_metadata)
                            print(f"📣 Broadcasting promo: {promo['name']}")
                            
                            # Update Icecast metadata
                            self.update_icecast_metadata(f"{promo['promoter']} - {promo['name']}")
                            
                            # Stream the promo
                            try:
                                
                                ok = await self.stream_single_file(promo_path, self.writer)

                                # reconnect if broken
                                if not ok:
                                    print("[STREAM] ⚡ Reconnecting after writer failure...")
                                    self.connected = False
                                    await asyncio.sleep(3)
                                    continue
                                
                                # Now_playing broadcast
                                np_data = self.songHandler.get_now_playing_data()

                                if np_data:
                                    print(f"[DEBUG] Now-playing data prepared: {np_data.title} - {np_data.artist}")
                                    await broadcast_song("now_playing", "notification", np_data)
                                    print("📣 Broadcasted NOW PLAYING to clients ✅")
                                
                                # Update play stats
                                self.promo_manager._update_play_stats(promo['id'])
                            except Exception as e:
                                print(f"⚠️ Error streaming promo: {e}")
                        else:
                            print(f"⚠️ Promo file not found: {promo_path}")
                            
                    # Reset song counter after playing promos
                    self.promo_manager.reset_song_count()
                    print("✅ Finished playing promos, reset song counter")
                else:
                    print("⏭️ Promo time but no active promos, resetting counter")
                    self.promo_manager.reset_song_count()
                
            # === NORMAL STREAM FLOW ===
            
            taskgroup.manage_task("song_downloader", self.DJ.song_downloader())
            
            # get next song or silence
            song_path = await self.get_next_song()
            if not song_path:
                song_path = str(self.silence_file) if self.silence_file.exists() else None
                if song_path:
                    print("[STREAM] 💤 Playlist empty — streaming silence...")
                else:
                    print("[STREAM] ⚠️ No Nothing.mp3 found — waiting 5s...")
                    await asyncio.sleep(5)
                    continue
                
            ok = await self.stream_single_file(song_path, self.writer)
            
            try:
                await self.songHandler.add_to_history()
                await self.songHandler.move_to_now_playing()

                # Always broadcast next_coming reset
                await broadcast_song("next_coming", "notification", None)
                print("📣 Broadcasted empty next_coming (song changed)")

                # Now_playing broadcast
                np_data = self.songHandler.get_now_playing_data()

                if np_data:
                    print(f"[DEBUG] Now-playing data prepared: {np_data.title} - {np_data.artist}")
                    await broadcast_song("now_playing", "notification", np_data)
                    print("📣 Broadcasted NOW PLAYING to clients ✅")

                    # ✅ Safe Icecast update
                    song_detail = f"{np_data.title} - {np_data.album}"
                else:
                    print("[DEBUG] No now-playing data found — broadcast skipped ❌")
                    song_detail = "Silence - Pew Hits"

                self.update_icecast_metadata(song_detail)

                # Queue broadcast
                queue = self.requestHandler.get_requests()
                queue_data = [asdict(song) for song in queue] if queue else []
                await broadcast_queue_update(queue_data)
                print("📣 Broadcasted queue update to clients")

                print(f"[STREAM_LOOP] 🎧 Now streaming: {os.path.basename(song_path)}")
                taskgroup.manage_task("update_position", self.songHandler.update_position_and_remaining())

                # Increment song counter after each successful song (including fallback)
                self.promo_manager.increment_song_count()
                print(f"📊 Songs since last promo: {self.promo_manager.songs_since_last_promo}/{self.promo_manager.promo_interval}")

            except Exception as e:
                print(f"[BROADCAST] ⚠️ Error during post-stream broadcast: {e}")
                traceback.print_exc()
                
            # reconnect if broken
            if not ok:
                print("[STREAM] ⚡ Reconnecting after writer failure...")
                self.connected = False
                await asyncio.sleep(3)
                continue
                
            
            # If silence is playing, recheck playlist after every loop
            if Path(song_path) == self.silence_file:
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(0.1)
                
                
    async def stream_manager(self):
        """Manages Icecast connection and streaming."""
        while True:
            ok = await self.connect_to_icecast()
            if not ok:
                print(f"[MAIN] Retry connecting in {config.RECONNECT_DELAY}s...")
                await asyncio.sleep(config.RECONNECT_DELAY)
                continue
                
            print("[MAIN] ✅ Connected to Icecast. Starting stream loop...")
            try:
                success = await self.stream_audio_loop()
                if not success:
                    print("[MAIN] Stream failed, closing socket and reconnecting...")
                    ok.close()
                    await asyncio.sleep(config.RECONNECT_DELAY)
                    continue
            except Exception as e:
                print(f"[MAIN] 💥 Error during streaming: {e}")
                await asyncio.sleep(config.RECONNECT_DELAY)
            finally:
                if self.writer:
                    try:
                        self.writer.close()
                        await self.writer.wait_closed()
                        print("[MAIN] 🔌 Closed Icecast connection.")
                    except Exception:
                        pass
                    
    async def promo_cleanup_task(self):
        """Periodically clean up expired promotions (every hour)"""
        while True:
            try:
                await asyncio.sleep(3600)  # Wait 1 hour
                print("🧹 Running automatic promo cleanup...")
                deleted_count = self.promo_manager.cleanup_expired_promotions()
                if deleted_count > 0:
                    print(f"✅ Cleaned up {deleted_count} expired promotion(s)")
                else:
                    print("✅ No expired promotions to clean up")
            except Exception as e:
                print(f"⚠️ Error during promo cleanup: {e}")
                
    async def clean_memory_periodically(self, interval: int = 300):
        """
        Periodically free unused memory and log usage.
        interval: seconds between each cleanup (default 5 minutes)
        """
        process = psutil.Process(os.getpid())

        while True:
            # Get memory usage before cleaning
            mem_before = process.memory_info().rss / (1024 ** 2)

            # Run garbage collection
            gc.collect()

            # Attempt to release memory back to OS (Linux-specific)
            try:
                import ctypes
                libc = ctypes.CDLL("libc.so.6")
                libc.malloc_trim(0)
            except Exception:
                pass

            # Get memory usage after cleaning
            mem_after = process.memory_info().rss / (1024 ** 2)
            freed = mem_before - mem_after

            print(f"Memory cleaned: {freed:.2f} MB freed (Current: {mem_after:.2f} MB)")
            await asyncio.sleep(interval)
            
            
    async def fetch_listeners(self):
        """
        Fetch current listener count for the given mountpoint from Icecast.
        """
        try:
            host=f"http://{config.SERVER_HOST}:{config.SERVER_PORT}/status-json.xsl"
            mount=config.MOUNT_POINT
            async with aiohttp.ClientSession() as session:
                async with session.get(host, timeout=5) as resp:
                    if resp.status != 200:
                        print(f"Icecast status fetch failed: HTTP {resp.status}")
                        return None

                    data = await resp.json()

                    # Data structure: data['icestats']['source'] can be list or dict
                    sources = data.get("icestats", {}).get("source", [])
                    if isinstance(sources, dict):
                        sources = [sources]

                    for src in sources:
                        if src.get("listenurl", "").endswith(mount):
                            return src.get("listeners", 0)

                    return 0  # If mount not found
        except Exception as e:
            print(f"Error fetching Icecast listeners: {e}")
            return None


    async def monitor_listeners(self, interval: int = 10):
        """
        Periodically fetch and log Icecast listener count.
        """
        while True:
            count = await self.fetch_listeners()
            if count is not None:
                await broadcast_listeners_update(count)
            else:
                print("Failed to retrieve listener count.")
            await asyncio.sleep(interval)
                
                
    async def main(self):
        print("🚀 Launching all systems!\n")

        # Start all services concurrently
        await asyncio.gather(
            start_ws_server_async(),            # Unified FastAPI server (WebSocket + REST API + Dashboard) on port 9626
            self.stream_manager(),              # Icecast streaming with metadata updates
            self.promo_cleanup_task(),          # Automatic cleanup of expired promotions (every hour)
            self.clean_memory_periodically(),   # Periodic memory cleanup
            self.monitor_listeners()            # Periodic listener count fetch
        )


if __name__ == "__main__":
    streamManager = StreamManager()
    asyncio.run(streamManager.main())