"""
Path translation utilities for handling Docker and host paths.

Provides robust path handling for different environments:
- Docker containers (/app/...)
- Host machines (/Volumes/..., /home/..., etc.)
- Windows paths (C:\\...)
"""
import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PathTranslator:
    """Translate paths between Docker and host environments."""

    def __init__(self):
        """Initialize path translator with environment detection."""
        self.is_docker = os.path.exists('/.dockerenv')
        self.host_prefix = os.getenv('HOST_PATH_PREFIX', '/Volumes/EDITING TERABYTE')
        self.docker_prefix = '/app'

        logger.info(f"PathTranslator initialized: is_docker={self.is_docker}, host_prefix={self.host_prefix}")

    def to_accessible_path(self, path: str) -> str:
        """
        Convert path to format accessible from current environment.

        Args:
            path: Path to convert

        Returns:
            Accessible path string
        """
        if not path:
            return path

        path_str = str(path)

        if self.is_docker:
            # Running in Docker, need Docker paths
            if path_str.startswith(self.host_prefix):
                # Convert host path to Docker path
                relative = path_str[len(self.host_prefix):].lstrip('/')
                docker_path = f"{self.docker_prefix}/{relative}"
                logger.debug(f"Converted host path to Docker: {path_str} -> {docker_path}")
                return docker_path
            elif path_str.startswith('/Volumes/') or path_str.startswith('/home/') or path_str.startswith('/Users/'):
                # Generic host path - try to map to Docker
                # Extract the relative part after the mount point
                parts = Path(path_str).parts
                if len(parts) > 2:
                    # Assume first two parts are mount point, rest is relative
                    relative = '/'.join(parts[2:])
                    docker_path = f"{self.docker_prefix}/{relative}"
                    logger.debug(f"Converted generic host path to Docker: {path_str} -> {docker_path}")
                    return docker_path
        else:
            # Running on host, need host paths
            if path_str.startswith(self.docker_prefix):
                # Convert Docker path to host path
                relative = path_str[len(self.docker_prefix):].lstrip('/')
                host_path = f"{self.host_prefix}/{relative}"
                logger.debug(f"Converted Docker path to host: {path_str} -> {host_path}")
                return host_path

        # Path is already in correct format or can't be translated
        return path_str

    def validate_file_exists(self, path: str) -> bool:
        """
        Check if file exists in current environment.

        Args:
            path: Path to check

        Returns:
            True if file exists, False otherwise
        """
        accessible_path = self.to_accessible_path(path)
        exists = Path(accessible_path).exists()

        if not exists:
            logger.warning(f"File not found: {accessible_path} (original: {path})")
        else:
            logger.debug(f"File exists: {accessible_path}")

        return exists

    def ensure_directory_exists(self, path: str) -> Path:
        """
        Ensure directory exists and return Path object.

        Args:
            path: Directory path

        Returns:
            Path object for the directory
        """
        accessible_path = self.to_accessible_path(path)
        dir_path = Path(accessible_path)

        dir_path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory exists: {accessible_path}")

        return dir_path

    def translate_bulk_paths(self, paths: list[str]) -> list[str]:
        """
        Translate multiple paths at once.

        Args:
            paths: List of paths to translate

        Returns:
            List of translated paths
        """
        return [self.to_accessible_path(p) for p in paths]


# Global translator instance
translator = PathTranslator()


def get_accessible_path(path: str) -> str:
    """
    Convenience function to get accessible path.

    Args:
        path: Path to translate

    Returns:
        Accessible path string
    """
    return translator.to_accessible_path(path)


def validate_video_path(path: str) -> str:
    """
    Validate and translate a video file path.

    Args:
        path: Video file path

    Returns:
        Accessible video path

    Raises:
        FileNotFoundError: If video file doesn't exist
        ValueError: If path is invalid
    """
    if not path:
        raise ValueError("Video path cannot be empty")

    accessible_path = translator.to_accessible_path(path)

    if not translator.validate_file_exists(path):
        # Try some common variations
        variations = [
            path,
            accessible_path,
            str(Path(path).resolve()),
        ]

        for variant in variations:
            if Path(variant).exists():
                logger.info(f"Found video at: {variant}")
                return variant

        raise FileNotFoundError(
            f"Video file not found: {path}\n"
            f"Accessible path: {accessible_path}\n"
            f"Current environment: {'Docker' if translator.is_docker else 'Host'}\n"
            f"Tried variations: {variations}"
        )

    return accessible_path
