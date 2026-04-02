import os
import json
import logging
import subprocess

from io import BytesIO
from tempfile import NamedTemporaryFile

from django.core.files import File
from django.utils.translation import gettext_lazy as _

from core import settings

logger = logging.getLogger(__name__)


def get_optimal_resolution(input_file):
    """
    Detect video resolution and return optimal target resolution for stories.
    Uses ffprobe to get video dimensions.
    """
    if hasattr(input_file, "temporary_file_path"):
        input_path = input_file.temporary_file_path()
    elif hasattr(input_file, "path"):
        input_path = input_file.path
    else:
        temp_file = NamedTemporaryFile(delete=False)
        for chunk in input_file.chunks():
            temp_file.write(chunk)
        temp_file.flush()
        temp_file.close()
        input_path = temp_file.name

    try:
        # Use ffprobe to get video info
        command = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            input_path,
        ]

        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    width = stream.get("width", 0)
                    height = stream.get("height", 0)

                    # Determine best target resolution
                    max_dimension = max(width, height)

                    if max_dimension >= 2160:  # 4K
                        return "1080"  # Downscale 4K to 1080p
                    elif max_dimension >= 1080:  # Full HD
                        return "720"  # Downscale to 720p
                    elif max_dimension >= 720:  # HD
                        return "720"  # Keep at 720p
                    else:  # Lower than 720p
                        return "480"  # Keep at 480p

        # Default fallback
        return "720"

    finally:
        # Cleanup temp file if created
        if not (
            hasattr(input_file, "temporary_file_path") or hasattr(input_file, "path")
        ):
            try:
                os.unlink(input_path)
            except:
                pass


def compress_video(input_file, target_resolution=None):
    """Compress video to a given resolution (480 / 720 / 1080)"""

    extension = input_file.name.split(".")[-1].lower()
    if extension not in settings.ALLOWED_VIDEO_EXTENSION:
        raise ValueError(_("Invalid video format, allowed are: mp4, mov, avi, mkv"))

    if target_resolution is None:
        target_resolution = get_optimal_resolution(input_file)
        logger.info(f"Auto-detected optimal resolution: {target_resolution}p")

    resolution_map = {
        "480": "854:480",
        "720": "1280:720",
        "1080": "1920:1080",
    }

    input_temp = None
    output_temp = None

    try:
        input_temp = NamedTemporaryFile(suffix=f".{extension}", delete=False)

        # Prepare input file path
        if hasattr(input_file, "read"):
            input_file.seek(0)
            input_temp.write(input_file.read())
        elif hasattr(input_file, "chunks"):
            for chunk in input_file.chunks():
                input_temp.write(chunk)
        else:
            raise ValueError(_("Unsupported file type"))

        input_temp.flush()
        input_temp.close()
        input_path = input_temp.name

        if not os.path.exists(input_path):
            raise RuntimeError(f"Input temp file not created: {input_path}")

        output_temp = NamedTemporaryFile(suffix=".mp4", delete=False)
        output_temp.close()
        output_path = output_temp.name

        command = [
            "ffmpeg",
            "-i",
            input_path,
            "-vf",
            f"scale={resolution_map[target_resolution]}:force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-profile:v",
            "high",
            "-level",
            "4.0",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
            "-max_muxing_queue_size",
            "1024",
            "-y",
            output_path,
        ]

        logger.info(f"Compressing to {target_resolution}p for stories")

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg STDERR: {result.stderr}")
            raise RuntimeError(f"Video compression failed", {result.stderr})

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("Compressed video file is empty or missing")

        with open(output_path, "rb") as f:
            file_content = f.read()

        logger.info(
            f"Compressed: {input_file.size} → {len(file_content)} bytes ({target_resolution}p)"
        )

        output_name = f"{input_file.name.rsplit('.', 1)[0]}.mp4"
        return File(BytesIO(file_content), name=output_name)

    except subprocess.TimeoutExpired:
        logger.error("Video compression timed out after 5 minutes")
        raise RuntimeError(
            "Video compression took too long. Please upload a shorter video."
        )

    except Exception as e:
        logger.exception(f"Error during video compression: {str(e)}")
        raise

    finally:
        if (
            input_temp
            and hasattr(input_temp, "name")
            and os.path.exists(input_temp.name)
        ):
            try:
                os.unlink(input_temp.name)
            except Exception as e:
                logger.warning(f"Failed to delete temp input file: {e}")

        if (
            output_temp
            and hasattr(output_temp, "name")
            and os.path.exists(output_temp.name)
        ):
            try:
                os.unlink(output_temp.name)
            except Exception as e:
                logger.warning(f"Failed to delete temp output file: {e}")
