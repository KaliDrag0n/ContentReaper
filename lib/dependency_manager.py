# lib/dependency_manager.py
import sys
import os
import platform
import shutil
import requests
import zipfile
import stat
import tarfile
import logging

logger = logging.getLogger()

# --- Constants ---
FFMPEG_RELEASES = {
    "win64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "linux64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz",
    "macos64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-macos64-gpl.zip"
}
FFMPEG_FALLBACK_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2024-07-21-12-38/ffmpeg-n6.1.1-13-g753e632512-win64-gpl.zip" # Example fallback

YT_DLP_API_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
YT_DLP_FALLBACK_URL_TEMPLATE = "https://github.com/yt-dlp/yt-dlp/releases/download/2023.12.30/yt-dlp{ext}"


# --- Helper Functions ---

def get_platform_info():
    """
    Determines the operating system and architecture.
    Returns a string like 'win64', 'linux64', or 'macos64', or None if unsupported.
    """
    system = platform.system().lower()
    is_64bit = sys.maxsize > 2**32

    if not is_64bit:
        logger.warning("32-bit systems are not supported by this auto-downloader. Please install ffmpeg and yt-dlp manually.")
        return None

    if system == 'windows':
        return 'win64'
    elif system == 'linux':
        return 'linux64'
    elif system == 'darwin': # macOS
        return 'macos64'

    logger.warning(f"Unsupported operating system '{platform.system()}'.")
    return None

def find_binary(name):
    """
    Checks if a binary exists in the system's PATH using shutil.which.
    Returns the full path to the binary if found, otherwise None.
    """
    logger.info(f"Searching for '{name}' in system PATH...")
    path = shutil.which(name)
    if path:
        logger.info(f"Found '{name}' at: {path}")
    else:
        logger.info(f"'{name}' not found in system PATH.")
    return path

def download_file(url, dest_path):
    """
    Downloads a file from a URL to a destination, showing progress.
    Returns True on success, False on failure.
    """
    logger.info(f"Downloading from {url}...")
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            with open(dest_path, 'wb') as f:
                bytes_downloaded = 0
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    bytes_downloaded += len(chunk)
                    if total_size > 0:
                        progress = (bytes_downloaded / total_size) * 100
                        # Use sys.stdout directly for the progress bar to avoid spamming the log file
                        sys.stdout.write(f"\rProgress: {progress:.1f}%")
                        sys.stdout.flush()
        sys.stdout.write("\n") # Newline after progress bar
        logger.info("Download complete.")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download file: {e}")
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError as rm_e:
                logger.error(f"Failed to remove incomplete download {dest_path}: {rm_e}")
        return False

def extract_archive(archive_path, dest_dir):
    """
    Extracts a zip or tar.xz archive to a destination directory.
    Returns True on success, False on failure.
    """
    logger.info(f"Extracting {archive_path}...")
    try:
        if archive_path.endswith('.zip'):
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(dest_dir)
        elif archive_path.endswith('.tar.xz'):
            with tarfile.open(archive_path, 'r:xz') as tar_ref:
                tar_ref.extractall(path=dest_dir)
        else:
            logger.error(f"Unsupported archive format for {archive_path}")
            return False

        logger.info("Extraction complete.")
        return True
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as e:
        logger.error(f"Failed to extract archive: {e}")
        return False

def make_executable(path):
    """Makes a file executable (important for Linux/macOS)."""
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        logger.info(f"Made '{os.path.basename(path)}' executable.")
    except OSError as e:
        logger.warning(f"Could not make file executable: {e}")

# --- Main Dependency Handlers ---

def ensure_yt_dlp(bin_dir, platform_info):
    """
    Ensures yt-dlp is available, downloading it if necessary.
    Returns the path to the executable, or None on failure.
    """
    logger.info("--- Checking for yt-dlp ---")
    system_path = find_binary('yt-dlp')
    if system_path:
        return system_path

    logger.info("Attempting to use bundled version.")
    ext = ".exe" if platform_info == 'win64' else ""
    bundled_path = os.path.join(bin_dir, f"yt-dlp{ext}")

    if os.path.exists(bundled_path):
        logger.info(f"Found bundled yt-dlp at: {bundled_path}")
        return bundled_path

    logger.info("No bundled version found. Downloading...")
    url = None
    try:
        response = requests.get(YT_DLP_API_URL, timeout=10)
        response.raise_for_status()
        assets = response.json().get('assets', [])
        for asset in assets:
            if asset['name'] == f'yt-dlp{ext}':
                url = asset['browser_download_url']
                break
        if not url:
             raise ValueError("Could not find a suitable asset in the latest release.")
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"Could not fetch latest yt-dlp release from GitHub API: {e}")
        logger.warning("Falling back to a known-good version.")
        url = YT_DLP_FALLBACK_URL_TEMPLATE.format(ext=ext)

    if url and download_file(url, bundled_path):
        if platform_info != 'win64':
            make_executable(bundled_path)
        return bundled_path

    return None

def ensure_ffmpeg(bin_dir, platform_info):
    """
    Ensures ffmpeg is available, downloading it if necessary.
    Returns the path to the executable, or None on failure.
    """
    logger.info("--- Checking for ffmpeg ---")
    system_path = find_binary('ffmpeg')
    if system_path:
        return system_path

    logger.info("Attempting to use bundled version.")
    ext = ".exe" if platform_info == 'win64' else ""
    bundled_path = os.path.join(bin_dir, f"ffmpeg{ext}")

    if os.path.exists(bundled_path):
        logger.info(f"Found bundled ffmpeg at: {bundled_path}")
        return bundled_path

    logger.info("No bundled version found. Downloading...")
    url = FFMPEG_RELEASES.get(platform_info)
    if not url:
        logger.error(f"No ffmpeg download URL for platform '{platform_info}'.")
        return None

    temp_archive_path = os.path.join(bin_dir, os.path.basename(url))
    temp_extract_dir = os.path.join(bin_dir, "ffmpeg_temp")

    try:
        if not download_file(url, temp_archive_path):
            logger.warning("Primary ffmpeg download failed. Attempting fallback...")
            if not download_file(FFMPEG_FALLBACK_URL, temp_archive_path):
                return None

        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir)
        os.makedirs(temp_extract_dir)

        if not extract_archive(temp_archive_path, temp_extract_dir):
            return None

        found_ffmpeg_path = None
        for root, _, files in os.walk(temp_extract_dir):
            for file in files:
                if file.lower() == f"ffmpeg{ext}":
                    shutil.move(os.path.join(root, file), bundled_path)
                    found_ffmpeg_path = bundled_path
                    break
            if found_ffmpeg_path:
                break

        if found_ffmpeg_path:
            logger.info(f"Successfully bundled ffmpeg at: {found_ffmpeg_path}")
            if platform_info != 'win64':
                make_executable(found_ffmpeg_path)
            return found_ffmpeg_path
        else:
            logger.error("Could not find ffmpeg executable in the downloaded archive.")
            return None

    finally:
        try:
            if os.path.exists(temp_archive_path):
                os.remove(temp_archive_path)
            if os.path.exists(temp_extract_dir):
                shutil.rmtree(temp_extract_dir)
            logger.info("Cleanup of temporary ffmpeg files complete.")
        except OSError as e:
            logger.error(f"Error during ffmpeg cleanup: {e}")


def ensure_dependencies(app_root):
    """
    The main entry point function for dependency checking.
    It ensures yt-dlp and ffmpeg are available and returns their paths.
    """
    bin_dir = os.path.join(app_root, "bin")
    try:
        os.makedirs(bin_dir, exist_ok=True)
    except OSError as e:
        logger.critical(f"Could not create directory {bin_dir}: {e}")
        return None, None

    platform_info = get_platform_info()
    if not platform_info:
        logger.critical("Could not determine platform or platform is unsupported.")
        return None, None

    yt_dlp_path = ensure_yt_dlp(bin_dir, platform_info)
    ffmpeg_path = ensure_ffmpeg(bin_dir, platform_info)

    if not yt_dlp_path:
        logger.critical("FATAL: yt-dlp could not be found or installed. The application cannot continue.")
    if not ffmpeg_path:
        logger.warning("WARNING: ffmpeg could not be found or installed. Merging video/audio and format conversions will fail.")

    return yt_dlp_path, ffmpeg_path
