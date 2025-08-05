# lib/dependency_manager.py
import sys
import os
import platform
import shutil
import requests
import zipfile
import stat

# --- Constants ---
# URLs for ffmpeg downloads from BtbN's builds, which are widely recommended.
FFMPEG_RELEASES = {
    "win64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "linux64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz", # Note: This is a tar.xz, requires tarfile library
    "macos64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-macos64-gpl.zip"
}

# URL for the latest yt-dlp executable.
YT_DLP_URL_TEMPLATE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp{ext}"


# --- Helper Functions ---

def get_platform_info():
    """
    Determines the operating system and architecture.
    Returns a string like 'win64', 'linux64', or 'macos64'.
    """
    system = platform.system().lower()
    is_64bit = sys.maxsize > 2**32

    if not is_64bit:
        print("WARNING: 32-bit systems are not supported by this auto-downloader. Please install ffmpeg manually.")
        return None

    if system == 'windows':
        return 'win64'
    elif system == 'linux':
        return 'linux64'
    elif system == 'darwin': # macOS
        return 'macos64'
    
    return None

def find_binary(name):
    """
    Checks if a binary exists in the system's PATH.
    Returns the full path to the binary if found, otherwise None.
    """
    print(f"Searching for '{name}' in system PATH...")
    path = shutil.which(name)
    if path:
        print(f"Found '{name}' at: {path}")
    else:
        print(f"'{name}' not found in system PATH.")
    return path

def download_file(url, dest_path):
    """
    Downloads a file from a URL to a destination, showing progress.
    """
    print(f"Downloading from {url}...")
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
                        sys.stdout.write(f"\rProgress: {progress:.1f}%")
                        sys.stdout.flush()
        print("\nDownload complete.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"\nERROR: Failed to download file: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False

def extract_archive(archive_path, dest_dir):
    """
    Extracts a zip or tar.xz archive to a destination directory.
    """
    print(f"Extracting {archive_path}...")
    try:
        if archive_path.endswith('.zip'):
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(dest_dir)
        elif archive_path.endswith('.tar.xz'):
            # The tarfile module is part of the standard library
            import tarfile
            with tarfile.open(archive_path, 'r:xz') as tar_ref:
                tar_ref.extractall(path=dest_dir)
        else:
            print(f"ERROR: Unsupported archive format for {archive_path}")
            return False
        
        print("Extraction complete.")
        return True
    except Exception as e:
        print(f"ERROR: Failed to extract archive: {e}")
        return False

def make_executable(path):
    """
    Makes a file executable (important for Linux/macOS).
    """
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC)
        print(f"Made '{os.path.basename(path)}' executable.")
    except Exception as e:
        print(f"WARNING: Could not make file executable: {e}")

# --- Main Dependency Handlers ---

def ensure_yt_dlp(bin_dir, platform_info):
    """
    Ensures yt-dlp is available, downloading it if necessary.
    Returns the path to the executable.
    """
    print("\n--- Checking for yt-dlp ---")
    system_path = find_binary('yt-dlp')
    if system_path:
        return system_path

    print("Attempting to use bundled version.")
    ext = ".exe" if platform_info == 'win64' else ""
    bundled_path = os.path.join(bin_dir, f"yt-dlp{ext}")

    if os.path.exists(bundled_path):
        print(f"Found bundled yt-dlp at: {bundled_path}")
        return bundled_path

    print("No bundled version found. Downloading...")
    url = YT_DLP_URL_TEMPLATE.format(ext=ext)
    if download_file(url, bundled_path):
        if platform_info != 'win64':
            make_executable(bundled_path)
        return bundled_path
    
    return None

def ensure_ffmpeg(bin_dir, platform_info):
    """
    Ensures ffmpeg is available, downloading it if necessary.
    Returns the path to the executable.
    """
    print("\n--- Checking for ffmpeg ---")
    system_path = find_binary('ffmpeg')
    if system_path:
        return system_path

    print("Attempting to use bundled version.")
    ext = ".exe" if platform_info == 'win64' else ""
    bundled_path = os.path.join(bin_dir, f"ffmpeg{ext}")

    if os.path.exists(bundled_path):
        print(f"Found bundled ffmpeg at: {bundled_path}")
        return bundled_path

    print("No bundled version found. Downloading...")
    url = FFMPEG_RELEASES.get(platform_info)
    if not url:
        print(f"ERROR: No ffmpeg download URL for platform '{platform_info}'.")
        return None

    temp_archive_path = os.path.join(bin_dir, os.path.basename(url))
    if not download_file(url, temp_archive_path):
        return None

    temp_extract_dir = os.path.join(bin_dir, "ffmpeg_temp")
    if os.path.exists(temp_extract_dir):
        shutil.rmtree(temp_extract_dir)
    os.makedirs(temp_extract_dir)

    if not extract_archive(temp_archive_path, temp_extract_dir):
        return None

    # Find the ffmpeg executable within the extracted mess
    found_ffmpeg = False
    for root, _, files in os.walk(temp_extract_dir):
        for file in files:
            if file.lower() == f"ffmpeg{ext}":
                shutil.move(os.path.join(root, file), bundled_path)
                found_ffmpeg = True
                break
        if found_ffmpeg:
            break
    
    # Cleanup
    os.remove(temp_archive_path)
    shutil.rmtree(temp_extract_dir)

    if found_ffmpeg:
        print(f"Successfully bundled ffmpeg at: {bundled_path}")
        if platform_info != 'win64':
            make_executable(bundled_path)
        return bundled_path
    else:
        print("ERROR: Could not find ffmpeg executable in the downloaded archive.")
        return None


def ensure_dependencies(app_root):
    """
    The main entry point function for dependency checking.
    It ensures yt-dlp and ffmpeg are available and returns their paths.
    """
    print("--- [1/3] Initializing Dependency Manager ---")
    bin_dir = os.path.join(app_root, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    
    platform_info = get_platform_info()
    if not platform_info:
        print("FATAL: Could not determine platform or platform is unsupported.")
        return None, None

    yt_dlp_path = ensure_yt_dlp(bin_dir, platform_info)
    ffmpeg_path = ensure_ffmpeg(bin_dir, platform_info)

    if not yt_dlp_path:
        print("\nFATAL: yt-dlp could not be found or installed. The application cannot continue.")
    if not ffmpeg_path:
        print("\nFATAL: ffmpeg could not be found or installed. The application cannot continue.")
        
    return yt_dlp_path, ffmpeg_path

