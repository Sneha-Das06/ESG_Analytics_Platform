from pathlib import Path


def ensure_directory(path):
    """Create a directory if it does not already exist."""
    Path(path).mkdir(parents=True, exist_ok=True)


def get_file_path(base_path, filename):
    """Return a complete file path."""
    return Path(base_path) / filename