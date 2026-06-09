"""Image processing: base64 decode, file save, hash computation, collision handling
(merged from tools/image_gen/image_processor.py).

Handles the I/O pipeline for generated images: decodes base64, saves to disk
with proper directory creation, computes CRC32 hash, and resolves hash
collisions via suffix increment.
"""

from __future__ import annotations

import base64
import re
import time
import zlib
from pathlib import Path

from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FALLBACK_DIR = "Library/System/fidia"
SUPPORTED_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}
DEFAULT_EXTENSION = ".png"

# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug.

    Args:
        text: Input string to slugify.

    Returns:
        Lowercase slug with hyphens, max 60 chars.
    """
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    slug = slug.strip("-")
    return slug[:60]


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def compute_crc32(data: bytes) -> str:
    """Compute CRC32 hex digest of binary data.

    Args:
        data: Raw bytes to hash.

    Returns:
        8-character hex string.
    """
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"


# ---------------------------------------------------------------------------
# Resolution helpers (without Pillow)
# ---------------------------------------------------------------------------


def guess_resolution_from_bytes(data: bytes) -> str | None:
    """Try to determine image resolution from raw bytes without Pillow.

    Supports PNG (IHDR chunk), JPEG (SOF marker), and returns ``None``
    for other formats. Falls back to the configured estimate.

    Args:
        data: Raw image bytes.

    Returns:
        Resolution string like ``"1024x1024"``, or ``None``.
    """
    if len(data) < 32:
        return None

    # PNG: IHDR chunk at offset 16 (after 8-byte sig + 4 len + 4 type)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        try:
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            return f"{width}x{height}"
        except (IndexError, ValueError):
            return None

    # JPEG: find SOF0 marker (0xFF 0xC0)
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 1:
            if data[i] == 0xFF:
                marker = data[i + 1]
                if marker == 0xC0:  # SOF0
                    height = int.from_bytes(data[i + 5 : i + 7], "big")
                    width = int.from_bytes(data[i + 7 : i + 9], "big")
                    return f"{width}x{height}"
                if marker in (0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9):
                    i += 2
                    continue
                if marker == 0xDA:  # SOS — no more markers
                    break
                seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
                i += 2 + seg_len
            else:
                i += 1

    # WebP: VP8/VP8L/VP8X chunk
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        try:
            vp8x = data[12:16]
            if vp8x == b"VP8X":
                w = (int.from_bytes(data[24:27], "little") & 0xFFFFFF) + 1
                h = (int.from_bytes(data[27:30], "little") & 0xFFFFFF) + 1
                return f"{w}x{h}"
        except (IndexError, ValueError):
            pass

    return None


# ---------------------------------------------------------------------------
# Image processor
# ---------------------------------------------------------------------------


class ImageProcessor:
    """Handles base64 decoding, file saving, and hash computation.

    Args:
        base_dir: Primary output directory (resolved relative to CWD).
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir.resolve()

    def save_image(
        self,
        base64_data: str,
        prompt: str,
        mime_type: str = "image/png",
        resolution: str | None = None,
    ) -> dict:
        """Decode base64 image data, save to file, compute hash.

        Args:
            base64_data: Base64-encoded image data (no header).
            prompt: Original prompt (used for filename slug).
            mime_type: MIME type for extension detection.
            resolution: Known resolution string (e.g. "1024x1024").

        Returns:
            Dict with ``path``, ``hash``, ``size_bytes``, ``resolution`` keys.
        """
        raw = self._decode(base64_data)
        actual_resolution = resolution or guess_resolution_from_bytes(raw)
        file_hash = compute_crc32(raw)

        ext = SUPPORTED_EXTENSIONS.get(mime_type, DEFAULT_EXTENSION)
        output_path = self._resolve_output_path(prompt, file_hash, ext)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(raw)
        logger.info(f"Saved image: {output_path} ({len(raw)} bytes, hash={file_hash})")

        return {
            "path": str(output_path),
            "hash": file_hash,
            "size_bytes": len(raw),
            "resolution": actual_resolution or "<unknown>",
        }

    def _decode(self, base64_data: str) -> bytes:
        """Decode base64 data, stripping any header if present."""
        if "base64," in base64_data:
            base64_data = base64_data.split("base64,")[-1]
        base64_data = base64_data.strip()
        return base64.b64decode(base64_data)

    def _resolve_output_path(self, prompt: str, file_hash: str, ext: str) -> Path:
        """Resolve the final output path, handling hash collisions."""
        slug = slugify(prompt)
        base_name = f"{slug}-{file_hash}"

        candidate = self.base_dir / f"{base_name}{ext}"
        if not candidate.exists():
            return candidate

        logger.warning(f"Hash collision detected for {file_hash}, appending suffix")
        for suffix_num in range(1, 100):
            candidate = self.base_dir / f"{base_name}_{suffix_num:02d}{ext}"
            if not candidate.exists():
                return candidate

        ts = int(time.time())
        candidate = self.base_dir / f"{base_name}_{ts}{ext}"
        return candidate

    @staticmethod
    def resolve_output_dir(output_arg: str | None, fallback_base: str = FALLBACK_DIR) -> Path:
        """Resolve the output directory, with fallback if primary path fails.

        If the output path contains date templates (``YYYY``, ``MM``),
        they are expanded to the current date. Falls back to library dir
        if the primary path is not writable.

        Args:
            output_arg: User-provided output path template.
            fallback_base: Fallback relative path.

        Returns:
            Resolved writable Path.
        """
        from datetime import datetime

        now = datetime.now()

        if output_arg:
            path_str = output_arg
            path_str = path_str.replace("YYYY", now.strftime("%Y"))
            path_str = path_str.replace("MM", now.strftime("%m"))
            path_str = path_str.replace("DD", now.strftime("%d"))
            candidate = Path(path_str)

            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate

            try:
                candidate.mkdir(parents=True, exist_ok=True)
                test_file = candidate / ".write_test"
                test_file.write_text("")
                test_file.unlink()
                return candidate
            except (OSError, PermissionError):
                logger.warning(
                    f"Primary dir not writable: {candidate}, falling back to {fallback_base}"
                )

        fallback = Path.cwd() / fallback_base
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
