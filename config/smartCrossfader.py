import asyncio
import gc
import os
import tempfile, shutil
from mutagen.mp3 import MP3
from asyncio.subprocess import create_subprocess_exec, DEVNULL
from config.songHandler import fetch_current_file

async def async_crossfade_overlap(audio1, audio2, crossfade_duration=20):
    overlap_duration = crossfade_duration // 2

    try:
        print(f"Starting crossfade overlap between {audio1} and {audio2} with duration {crossfade_duration}s...")
        with tempfile.TemporaryDirectory() as tmpdir:
            # Paths
            part1_end = os.path.join(tmpdir, "audio1_end.mp3")
            part2_start = os.path.join(tmpdir, "audio2_start.mp3")
            mixed_overlap = os.path.join(tmpdir, "overlap_mix.mp3")
            trimmed_audio1 = os.path.join(tmpdir, "audio1_trimmed.mp3")
            trimmed_audio2 = os.path.join(tmpdir, "audio2_trimmed.mp3")
            
            audio2_final = os.path.join(tmpdir, "audio2_final.mp3")

            dur1 = MP3(audio1).info.length
            fade_out_start = dur1 - overlap_duration

            # Fade out last part of audio1
            await (await create_subprocess_exec(
                "ffmpeg", "-y", "-ss", str(fade_out_start), "-t", str(overlap_duration),
                "-i", audio1,
                "-af", f"afade=t=out:st=0:d={overlap_duration}:curve=cub",
                "-c:a", "libmp3lame", part1_end,
                stdout=DEVNULL, stderr=DEVNULL
            )).communicate()

            # Fade in first part of audio2
            await (await create_subprocess_exec(
                "ffmpeg", "-y", "-t", str(overlap_duration),
                "-i", audio2,
                "-af", f"afade=t=in:st=0:d={overlap_duration}:curve=cub",
                "-c:a", "libmp3lame", part2_start,
                stdout=DEVNULL, stderr=DEVNULL
            )).communicate()

            # Mix the overlap parts
            await (await create_subprocess_exec(
                "ffmpeg", "-y",
                "-i", part1_end,
                "-i", part2_start,
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0",
                "-c:a", "libmp3lame", mixed_overlap,
                stdout=DEVNULL, stderr=DEVNULL
            )).communicate()

            # Trim audio1 to remove last 'overlap_duration' seconds
            await (await create_subprocess_exec(
                "ffmpeg", "-y",
                "-i", audio1,
                "-t", str(fade_out_start),
                "-c:a", "libmp3lame", trimmed_audio1,
                stdout=DEVNULL, stderr=DEVNULL
            )).communicate()

            # Trim audio2 to remove first 'overlap_duration' seconds
            await (await create_subprocess_exec(
                "ffmpeg", "-y",
                "-ss", str(overlap_duration),
                "-i", audio2,
                "-c:a", "libmp3lame", trimmed_audio2,
                stdout=DEVNULL, stderr=DEVNULL
            )).communicate()

            # Combine: mixed_overlap + trimmed_audio2 → audio2_final
            await (await create_subprocess_exec(
                "ffmpeg", "-y",
                "-i", mixed_overlap,
                "-i", trimmed_audio2,
                "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1",
                "-c:a", "libmp3lame", audio2_final,
                stdout=DEVNULL, stderr=DEVNULL
            )).communicate()
            
            # Replace original files
            shutil.move(trimmed_audio1, audio1)
            shutil.move(audio2_final, audio2)

            print(f"✅ Trimmed Audio1 saved back to: {audio1}")
            print(f"✅ Overlapped Audio2 saved back to: {audio2}")
            del dur1
            import gc
            gc.collect()

    except Exception as e:
        print(f"💥 Error in async_crossfade_overlap: {e}")
        
async def crossfader():
    current_file = fetch_current_file()
    audio1 = current_file.get("now_playing")
    audio2 = current_file.get("next_coming")
    await async_crossfade_overlap(audio1, audio2)