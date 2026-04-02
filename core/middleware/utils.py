import os
import gzip
import shutil

from logging.handlers import TimedRotatingFileHandler


class CompressedTimedRotatingFileHandler(TimedRotatingFileHandler):
    @staticmethod
    def rotator(source, dest):
        """Compress rotated log files. Windows: ignore PermissionError on remove."""
        try:
            with open(source, "rb") as f_in:
                with gzip.open(f"{dest}.gz", "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            try:
                os.remove(source)
            except OSError:
                pass  # Windows: fayl hali qulflangan bo‘lishi mumkin
        except OSError:
            pass
