import requests, os, zipfile, io, shutil, time

# --- Configuration ---
# This should match the slug in your web_tool.py
GITHUB_REPO_SLUG = "KaliDrag0n/Downloader-Web-UI"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest"
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

def print_status(message):
    """Prints a status message to the console."""
    print(f"[UPDATER] {message}")

def get_latest_release_info():
    """Fetches the latest release information from GitHub."""
    print_status("Fetching latest release information...")
    try:
        response = requests.get(API_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        zip_url = data.get("zipball_url")
        tag_name = data.get("tag_name")
        if not zip_url or not tag_name:
            raise ValueError("Could not find zipball_url or tag_name in the release info.")
        print_status(f"Found latest version: {tag_name}")
        return zip_url
    except Exception as e:
        print_status(f"ERROR: Could not fetch release info: {e}")
        return None

def download_and_unzip(url):
    """Downloads and unzips the release archive to a temporary directory."""
    print_status(f"Downloading update from {url}...")
    temp_dir = os.path.join(CURRENT_DIR, ".temp_update")
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            # The zip file from GitHub contains a single root folder. We need to extract its contents.
            root_folder_name = z.namelist()[0] 
            print_status(f"Extracting files to temporary directory: {temp_dir}")
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            os.makedirs(temp_dir)
            z.extractall(temp_dir)
        
        # Return the path to the actual content, inside the root folder.
        return os.path.join(temp_dir, root_folder_name)

    except Exception as e:
        print_status(f"ERROR: Failed to download or unzip update: {e}")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        return None

def apply_update(source_dir):
    """Copies the new files from the source directory to the application's root directory."""
    print_status("Applying update...")
    if not source_dir or not os.path.exists(source_dir):
        print_status("ERROR: Update source directory not found. Aborting.")
        return False

    try:
        # List of files/folders to preserve in the original directory
        preserved_items = [
            "downloads",  # Don't overwrite the user's downloads
            ".temp",      # Don't overwrite temporary download files
            "logs",       # Preserve log files
            "config.json",# Preserve user configuration
            "state.json", # Preserve queue/history
            "cookies.txt",# Preserve cookies
            ".git"        # Preserve git history if it exists
        ]

        for item in os.listdir(source_dir):
            source_item_path = os.path.join(source_dir, item)
            dest_item_path = os.path.join(CURRENT_DIR, item)

            if item in preserved_items:
                print_status(f"Skipping preserved item: {item}")
                continue

            print_status(f"Updating: {item}")
            if os.path.isdir(source_item_path):
                if os.path.exists(dest_item_path):
                    shutil.rmtree(dest_item_path)
                shutil.copytree(source_item_path, dest_item_path)
            else: # It's a file
                shutil.copy2(source_item_path, dest_item_path)
        
        print_status("Update applied successfully.")
        return True
    except Exception as e:
        print_status(f"ERROR: An error occurred while applying the update: {e}")
        return False
    finally:
        # Clean up the temporary update folder
        temp_update_root = os.path.dirname(source_dir)
        if os.path.exists(temp_update_root):
             print_status(f"Cleaning up temporary folder: {temp_update_root}")
             shutil.rmtree(temp_update_root)


if __name__ == "__main__":
    print_status("--- Windows Auto-Updater Initialized ---")
    zip_url = get_latest_release_info()
    if zip_url:
        update_source = download_and_unzip(zip_url)
        if update_source:
            apply_update(update_source)
    
    print_status("--- Update Process Finished ---")
    # Pause at the end so the user can see the result if run manually
    time.sleep(5)
