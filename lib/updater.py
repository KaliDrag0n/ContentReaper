import os
import subprocess
import sys
import time
import requests
import zipfile
import shutil
import io

GITHUB_REPO_SLUG = "KaliDrag0n/Downloader-Web-UI"

def update_via_git(project_root):
    """Performs an update using git commands."""
    print("Updater: Git repository detected. Attempting update via git...")
    try:
        print("Updater: Fetching latest release information from GitHub API...")
        latest_tag = None
        try:
            res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest", timeout=15)
            res.raise_for_status()
            latest_tag = res.json().get("tag_name")
        except Exception as e:
            print(f"Updater: CRITICAL: Could not fetch release information from GitHub API: {e}")
            print("Updater: Aborting update to prevent installing an unstable version.")
            return False

        if latest_tag:
            print(f"Updater: Found latest release: {latest_tag}. Checking it out...")
            subprocess.run(["git", "fetch", "--tags", "--force"], check=True, cwd=project_root)
            subprocess.run(["git", "checkout", latest_tag], check=True, cwd=project_root)
            print(f"Updater: Successfully checked out release {latest_tag}.")
            return True
        else:
            print("Updater: CRITICAL: No release tag found in API response. Aborting update.")
            return False
            
    except subprocess.CalledProcessError as e:
        print(f"Updater: An error occurred during the git update process: {e}")
    except FileNotFoundError:
        print("Updater: A command was not found. Is git installed and in the system's PATH?")
    return False

def update_via_zip(project_root):
    """Performs an update by downloading and extracting the latest release ZIP."""
    print("Updater: No .git directory found. Attempting update via ZIP download...")
    try:
        print("Updater: Fetching latest release information...")
        res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest", timeout=15)
        res.raise_for_status()
        release_data = res.json()
        zip_url = release_data.get("zipball_url")
        
        if not zip_url:
            print("Updater: CRITICAL: Could not find ZIP URL in API response. Aborting.")
            return False

        print(f"Updater: Downloading release from {zip_url}...")
        res = requests.get(zip_url, timeout=60)
        res.raise_for_status()
        
        zip_file = zipfile.ZipFile(io.BytesIO(res.content))
        
        # The top-level directory in the zip is usually something like 'user-repo-commit'
        top_level_dir = zip_file.namelist()[0]
        
        temp_extract_dir = os.path.join(project_root, "update_temp")
        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir)
            
        print(f"Updater: Extracting to temporary directory: {temp_extract_dir}")
        zip_file.extractall(temp_extract_dir)
        
        update_source_dir = os.path.join(temp_extract_dir, top_level_dir)
        
        print("Updater: Overwriting old files with new version...")
        # Copy files from the extracted folder to the project root
        for item in os.listdir(update_source_dir):
            source_item = os.path.join(update_source_dir, item)
            dest_item = os.path.join(project_root, item)
            # Do not overwrite the user's data directory
            if item == 'data':
                continue
            if os.path.isdir(source_item):
                if os.path.exists(dest_item):
                    shutil.rmtree(dest_item)
                shutil.copytree(source_item, dest_item)
            else:
                shutil.copy2(source_item, dest_item)
        
        print("Updater: File copy complete.")
        return True

    except Exception as e:
        print(f"Updater: An error occurred during the ZIP update process: {e}")
        return False
    finally:
        # Cleanup
        if 'temp_extract_dir' in locals() and os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir)

def main():
    """
    This script handles the application update process.
    It waits for the main application to shut down, pulls the latest code,
    updates dependencies, and then exits, allowing a service manager to restart the app.
    """
    print("Updater: Waiting for main application to close...")
    time.sleep(5)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    print(f"Updater: Working directory set to: {project_root}")

    update_succeeded = False
    if os.path.isdir(os.path.join(project_root, ".git")):
        update_succeeded = update_via_git(project_root)
    else:
        update_succeeded = update_via_zip(project_root)

    if not update_succeeded:
        print("Updater: The application was not updated. Please check the errors above.")
        sys.exit(1)
        
    try:
        print("Updater: Installing/updating dependencies...")
        pip_command = [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt']
        subprocess.run(pip_command, check=True, cwd=project_root)
        print("Updater: Dependencies are up to date.")

        print("\nUpdater: Update process completed successfully.")
        print("Updater: The application will be restarted by systemd or needs to be started manually.")

    except subprocess.CalledProcessError as e:
        print(f"Updater: An error occurred during dependency installation: {e}")
        print("Updater: Update partially failed. Please run 'pip install -r requirements.txt' manually.")
    finally:
        print("Updater: Exiting.")

if __name__ == "__main__":
    main()
