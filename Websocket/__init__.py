import os, sys, threading
from quattro import TaskGroup
from dataclasses import asdict
from fastapi import WebSocket
from cattrs.preconf.json import make_converter
from Websocket.models import SessionMetadata, NowPlayingResponse, QueueResponse, BlocklistResponse, Error, SkipSongResponse, UnblockSongResponse, BlockSongResponse, RemoveSongResponse, PlaySongResponse, ReloadAllResponse
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config.BG_process_status import songDownloader
from typing import Literal, Union, Type, Any, TypeVar
import importlib
import sys, asyncio
import os

class PewHits:
    """A Base class for PewHits.
    Here all the WebSocket GET functions will be defined."""
    
    async def before_start(tg: TaskGroup) -> None:
        """Called before the radio starts."""
        pass

    async def on_start(
        websocket, 
        action: Literal["notification", "response"], 
        session_metadata: SessionMetadata
    ) -> None:
        """On a connection to the radio being established.

        This may be called multiple times, since the connection may be dropped
        and reestablished.

        The 'action' parameter will decide whether it's a notification or a response.
        """
        if action == "notification":
            # This is an automatic notification, sending metadata
            await websocket.send_json({
                "action": "on_start", 
                "type": "notification", 
                "session_metadata": session_metadata
            })
            
    async def on_start_now_playing(
        websocket: WebSocket, 
        action: Literal["notification", "response"]
    ) -> None:
        """
        WebSocket: Send the currently playing song as a notification or response.
        """
        
        from config.songHandler import songHandler
        
        response_base = {
            "action": "now_playing",
            "type": action,
            "rid": None,
        }

        song = songHandler.get_now_playing_data()

        if song:
            await PewHitsServer.send_success(
                websocket,
                response_base,
                message="Now playing song found.",
                data=asdict(song),
                response_cls=NowPlayingResponse
            )
        else:
            await PewHitsServer.send_error(
                websocket,
                response_base,
                message="No song is currently playing.",
                code=404
            )
    
    async def now_playing(
        websocket: WebSocket,
        data: dict,
        action: Literal["notification", "response"]
    ) -> None:
        """
        WebSocket: Send the currently playing song as a notification or response.
        """
        
        from config.songHandler import songHandler
        
        rid = data.get("rid")
        response_base = {
            "action": "now_playing",
            "type": action,
            "rid": rid,
        }
        song = songHandler.get_now_playing_data()

        if song:
            await PewHitsServer.send_success(
                websocket,
                response_base,
                message="Now playing song found.",
                data=asdict(song),
                response_cls=NowPlayingResponse
            )
        else:
            await PewHitsServer.send_error(
                websocket,
                response_base,
                message="No song is currently playing.",
                code=404
            )
        
    async def next_coming(
        websocket: WebSocket,
        data: dict,
        action: Literal["notification", "response"]
    ) -> None:
        """
        WebSocket: Send the next queued song as a notification or response.
        """
        from config.songHandler import songHandler
        
        rid = data.get("rid")
        response_base = {
            "action": "next_coming",
            "type": action,
            "rid": rid,
        }
        song = songHandler.get_next_coming_data()

        if song:
            await PewHitsServer.send_success(
                websocket,
                response_base,
                message="Next song in queue retrieved.",
                data=asdict(song),
                response_cls=NowPlayingResponse
            )
        else:
            await PewHitsServer.send_error(
                websocket,
                response_base,
                message="No song is coming up next.",
                code=404
            )
    
    async def queue(
        websocket: WebSocket,
        data: dict,
        action: Literal["notification", "response"]
    ) -> None:
        """
        WebSocket: Send the current song queue as a notification or response.
        """
        
        from config.requestHandler import requestHandler
        
        rid = data.get("rid")
        queue_data = requestHandler.get_requests()  # Should return a list of QueueSong instances
        
        response_base = {
            "action": "queue",
            "type": action,
            "rid": rid,
        }

        if not queue_data:
            await PewHitsServer.send_error(
                websocket,
                response_base,
                message="Queue is empty.",
                code=404
            )
            return

        requests_serialized = [asdict(song) for song in queue_data]

        await PewHitsServer.send_success(
            websocket,
            response_base,
            message="Current song queue retrieved.",
            data=requests_serialized,
            response_cls=QueueResponse
        )
    
    async def blocklist(
        websocket: WebSocket,
        data: dict,
        action: Literal["notification", "response"]
    ) -> None:
        """Send the list of blocked songs via WebSocket."""
        from config.blocker import blocker
        
        rid = data.get("rid")
        blocked_songs = blocker.list_blocked_songs()
        
        response_base = {
            "action": "blocklist",
            "type": action,
            "rid": rid,
        }

        if not blocked_songs:
            await PewHitsServer.send_error(
                websocket,
                response_base, 
                message="No blocked songs found.",
                code=404
            )
        
        await PewHitsServer.send_success(
            websocket, 
            response_base, 
            message="Blocked songs list retrieved successfully.",
            data=[asdict(song) for song in blocked_songs],
            response_cls=BlocklistResponse
        )
    
class PewHitsServer:
    """Here all Websocket POST functions will be defined."""
    
    # 🎯 Generic Success + Error TypeVar
    T = TypeVar("T")
    E = TypeVar("E")
        
    # 🚨 Standardized Error builder
    @staticmethod
    def build_error(action: str, type_: str, rid: str, message: str, code: int) -> Error:
        return Error(
            action=action,
            type=type_,
            rid=rid,
            code=code,
            message=message,
            error=message,
            data=""
        )

    # 🎯 Reusable success sender
    @staticmethod
    async def send_success(
        websocket: WebSocket,
        response_base: dict,
        message: str,
        data: Union[str, dict, Any],
        response_cls: Type[T]
    ) -> T:
        payload = {
            **response_base,
            "code": 200,
            "message": message,
            "data": data
        }
        response = response_cls(**payload)
        await websocket.send_json(asdict(response))
        return response

    # 🚨 Reusable error sender
    @staticmethod
    async def send_error(
        websocket: WebSocket,
        response_base: dict,
        message: str,
        code: int,
        error_cls: Type[E] = Error
    ) -> E:
        error = error_cls(**{
            **response_base,
            "code": code,
            "message": message,
            "error": message,
            "data": ""
        })
        await websocket.send_json(asdict(error))
        return error
    
    async def skip_current_song(
        websocket: WebSocket,
        data: dict,
        action: Literal["notification", "response"]
    ) -> None:
        """Skip the currently playing song (WebSocket version)."""
        
        from config.songHandler import songHandler
        
        rid = data.get("rid")
        DJ = data.get("DJ", False)

        response_base = {
            "action": "skip_current_song",
            "type": action,
            "rid": rid,
        }

        try:
            if not DJ:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="You are not authorized to skip songs.",
                    code=806
                )
                return
            if songDownloader.song_downloader:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Cannot skip songs while the next song is downloading.",
                    code=709
                )
                return


            now_playing_data = songHandler.get_now_playing_data()
            if not now_playing_data:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="No song is currently playing.",
                    code=404
                )
                return

            from config.BG_process_status import skip
            skip.skip_status = True

            await PewHitsServer.send_success(
                websocket,
                response_base,
                message="Current song skipped successfully.",
                data="",
                response_cls=SkipSongResponse
            )

        except Exception as e:
            await PewHitsServer.send_error(
                websocket,
                response_base,
                message=f"Unexpected server error: {str(e)}",
                code=500
            )
        
    async def remove_blocked_song(
        websocket: WebSocket,
        data: dict,
        action: Literal["notification", "response"]
    ) -> None:
        """WebSocket: Remove a blocked song from the list."""
        
        from config.request_adder import requestAdder
        from config.blocker import blocker
        
        rid = data.get("rid")
        DJ = data.get("DJ", False)

        response_base = {
            "action": "unblock",
            "type": action,
            "rid": rid,
        }

        try:
            if not DJ:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="You are not authorized to unblock songs..", 
                    code=808)
                return
            if not isinstance(data, dict):
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Invalid data format. Expected JSON object.", 
                    code=700)
                return

            index = data.get("index")
            app_name = data.get("app")
            is_moderator = bool(data.get("is_moderator"))

            if index is None:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Missing 'index' in request.", 
                    code=701)
                return

            if not is_moderator:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="You are not authorized to remove blocked songs.", 
                    code=703)
                return

            app_id = requestAdder.get_app_id(app_name) if app_name else None
            if app_name and app_id is None:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message=f"Unknown app: {app_name}", 
                    code=608)
                return

            response = blocker.remove_song_by_index(index)

            if isinstance(response, str):  # Error scenario from your `remove_song_by_index`
                if "error 801" in response:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="Provide at least one valid song ID (Spotify or YouTube).", 
                        code=801)
                    return
                elif "error 804" in response:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="Blocked song with the provided index does not exist.", 
                        code=804)
                    return

            await PewHitsServer.send_success(
                websocket,
                response_base,
                message=f"Blocked song with index '{index}' removed successfully.", 
                data=response,
                response_cls=UnblockSongResponse)

        except Exception as e:
            await PewHitsServer.send_error(
                websocket,
                response_base,
                message=f"Unexpected server error: {str(e)}", 
                code=500)
            
    async def block_current_song(
        websocket: WebSocket,
        data: dict,
        action: Literal["notification", "response"]
    ) -> None:
        """WebSocket: Block the currently playing song."""
        
        from config.songHandler import songHandler
        from config.request_adder import requestAdder
        from config.blocker import blocker
        
        rid = data.get("rid")
        DJ = data.get("DJ", False)
        
        response_base = {
            "action": "block_current_song",
            "type": action,
            "rid": rid,
            "data": {},  # Always present
        }

        try:
            if not DJ:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="You are not authorized to block songs.",
                    code=807)
                return
            if not isinstance(data, dict):
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Invalid data format. Expected JSON object.", 
                    code=400)
                return

            moderator = bool(data.get("is_moderator"))
            app_name = data.get("app")

            # App validation
            app_id = requestAdder.get_app_id(app_name) if app_name else None
            if app_name and app_id is None:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message=f"Unknown app: {app_name}", 
                    code=608)
                return

            if not moderator:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="You are not authorized to block songs.", 
                    code=703)
                return
            
            if songDownloader.song_downloader:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Cannot block songs while the next song is downloading.",
                    code=709
                )
                return

            now_playing = songHandler.get_now_playing_data()
            if not now_playing:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="No song is currently playing.", 
                    code=404)
                return

            song = now_playing
            ID = song.ID
            title = song.title
            artist = song.artist
            album = song.album

            song_resp = blocker.add_song(ID, None, title, artist, album, blocker)

            if isinstance(song_resp, str):
                if "error 802" in song_resp:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="This song is already blocked.", 
                        code=802)
                    return
                elif "error 801" in song_resp:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="Provide at least one valid song ID (Spotify or YouTube).", 
                        code=801)
                    return

            # Refresh current now playing
            now_playing_data = songHandler.get_now_playing_data()
            if not now_playing_data:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="No song is currently playing.", 
                    code=404)
                return

            from config.BG_process_status import skip
            skip.skip_status = True
            

            await PewHitsServer.send_success(
                websocket,
                response_base,
                message="Song blocked successfully.", 
                data=song_resp,
                response_cls=BlockSongResponse)

        except Exception as e:
            await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message=f"Unexpected server error: {str(e)}", 
                    code=500)
            
    async def remove_song(
        websocket: WebSocket,
        data: dict,
        action: Literal["notification", "response"]
    ) -> None:
        """WebSocket: Remove a song from the queue."""
        
        from config.request_adder import requestAdder
        from config.requestHandler import requestHandler

        rid = data.get("rid")

        response_base = {
            "action": "remove_song",
            "type": action,
            "rid": rid,
            "data": {}
        }

        try:
            if not isinstance(data, dict):
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Invalid data format. Expected JSON object.", 
                    code=700)
                return

            song_index = data.get("index")
            requester = data.get("requester")
            moderator = str(data.get("moderator", False)).lower() == "true"
            app_name = data.get("app")

            # App check
            app_id = requestAdder.get_app_id(app_name) if app_name else None
            if app_name and app_id is None:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message=f"Unknown app: {app_name}", 
                    code=608)
                return

            if not requester:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Missing 'requester' field.", 
                    code=701)
                return

            try:
                index = int(index)
            except (ValueError, TypeError):
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Missing 'index' field to identify the song or Invalid 'index'. Must be an integer.", 
                    code=702)
                return

            song_data = await requestHandler.remove_request_by_index(song_index, requester, moderator)

            if isinstance(song_data, str):  # Error string returned
                if "error 701" in song_data:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="You are not authorized to remove this song request.", 
                        code=701)
                    return
                elif "error 702" in song_data:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="Request with the provided index does not exist.", 
                        code=702)
                    return
                elif "error 801" in song_data:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="Provide at least one valid song ID (Spotify or YouTube).", 
                        code=801)
                    return

            await PewHitsServer.send_success(
                websocket,
                response_base,
                message=f"Song at index '{song_index}' removed successfully.", 
                data=song_data,
                response_cls=RemoveSongResponse)

        except Exception as e:
            await PewHitsServer.send_error(
                websocket,
                response_base,
                message=f"Unexpected server error: {str(e)}", 
                code=500)
            
    async def play_song(
        websocket: WebSocket,
        data: dict,
        action: Literal["notification", "response"]
    ) -> None:
        """WebSocket: Play a song."""
        
        from config.request_adder import requestAdder
        
        rid = data.get("rid")

        response_base = {
            "action": "play_song",
            "type": action,
            "rid": rid,
            "data": {}
        }

        try:
            if not isinstance(data, dict):
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Invalid data format. Expected JSON object.", 
                    code=700)
                return

            song_id = data.get("song_id")
            requester = data.get("requester")
            app_name = data.get("app")

            if not song_id:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Missing 'song_id' field in request.", 
                    code=701)
                return

            if not requester:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Missing 'requester' field in request.", 
                    code=702)
                return

            if not app_name:
                await PewHitsServer.send_error(
                    websocket,
                    response_base,
                    message="Missing 'app' field in request.", 
                    code=703)
                return

            song_data = await requestAdder.request_maker(song_id, requester, app_name)

            if isinstance(song_data, str):
                if "Unknown app" in song_data:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message=song_data, 
                        code=608)
                elif "error 609" in song_data:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="Track recently played.", 
                        code=609)
                elif "error 709" in song_data:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="Requested song is blocked.", 
                        code=709)
                elif "error 708" in song_data:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="You have already requested this song, and it is waiting in the queue.", 
                        code=708)
                elif "error 707" in song_data:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="This song is already in the queue.", 
                        code=707)
                elif "error 706" in song_data:
                    await PewHitsServer.send_error(
                        websocket,
                        response_base,
                        message="This song is currently playing.", 
                        code=706)
                return

            # All went well
            await PewHitsServer.send_success(
                websocket,
                response_base,
                message="Song request added successfully.", 
                data=song_data,
                response_cls=PlaySongResponse)

        except Exception as e:
            await PewHitsServer.send_error(
                websocket,
                response_base,
                message=f"Unexpected server error: {str(e)}", 
                code=500)

    async def reloadall(
        websocket: WebSocket,
        data: dict,
        action: Literal["notification", "response"]
    ) -> None:
        """Websocket for reloading all files"""
        try:
            base_dirs = ["WebAPI", "config", "Websocket"]
            reloaded_modules = []
            rid = data.get("rid")
            response_base = {
                "action": "reloadall",
                "type": action,
                "rid": rid,
                "data": {}
            }

            print("♻️ Scanning and reloading all modules in WebAPI/, config/, Websocket/")

            for base in base_dirs:
                for root, dirs, files in os.walk(base):
                    for file in files:
                        if file.endswith(".py") and not file.startswith("__"):
                            module_path = os.path.join(root, file).replace("/", ".").replace("\\", ".").replace(".py", "")
                            if module_path in sys.modules:
                                importlib.reload(sys.modules[module_path])
                                reloaded_modules.append(module_path)
                            else:
                                __import__(module_path)
                                reloaded_modules.append(module_path)                   

            # 📩 Confirmation message
            msg = f"✅ Reloaded {len(reloaded_modules)} modules from src/ & config/. Ready to roll!"
            
            await PewHitsServer.send_success(
                websocket,
                response_base,
                message="Song request added successfully.", 
                data=msg,
                response_cls=ReloadAllResponse)

        except Exception as e:
            err = f"❌ Reload failed: {e}"
            print(err)
            await PewHitsServer.send_error(
                websocket,
                response_base,
                message=err, 
                code=500)
            
converter = make_converter()