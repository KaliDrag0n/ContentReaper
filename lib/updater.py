import os
import subprocess
import sys
import time
import requests
import zipfile
import shutil
import io
import logging

# Basic logger for the updater script
logging.basicConfig(level=logging.INFO, format='%(asctime)s - Updater: %(message)s')
logger = logging.getLogger()

GITHUB_REPO_SLUG = "KaliDrag0n/ContentReaper"

def update_via_git(project_root):
    """Performs an update using git commands."""
    logger.info("Git repository detected. Attempting update via git...")
    try:
        logger.info("Fetching latest release information from GitHub API...")
        latest_tag = None
        try:
            res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest", timeout=15)
            res.raise_for_status()
            latest_tag = res.json().get("tag_name")
        except requests.RequestException as e:
            logger.critical(f"Could not fetch release information from GitHub API: {e}")
            logger.critical("Aborting update to prevent installing an unstable version.")
            return False

        if latest_tag:
            logger.info(f"Found latest release: {latest_tag}. Checking it out...")
            subprocess.run(["git", "fetch", "--tags", "--force"], check=True, cwd=project_root)
            subprocess.run(["git", "checkout", latest_tag], check=True, cwd=project_root)
            logger.info(f"Successfully checked out release {latest_tag}.")
            return True
        else:
            logger.critical("No release tag found in API response. Aborting update.")
            return False

    except subprocess.CalledProcessError as e:
        logger.error(f"An error occurred during the git update process: {e}")
    except FileNotFoundError:
        logger.error("A command was not found. Is git installed and in the system's PATH?")
    return False

def update_via_zip(project_root):
    """Performs an update by downloading and extracting the latest release ZIP."""
    logger.info("No .git directory found. Attempting update via ZIP download...")
    temp_extract_dir = os.path.join(project_root, "update_temp")
    try:
        logger.info("Fetching latest release information...")
        res = requests.get(f"https://api.github.com/repos/{GITHUB_REPO_SLUG}/releases/latest", timeout=15)
        res.raise_for_status()
        release_data = res.json()
        zip_url = release_data.get("zipball_url")

        if not zip_url:
            logger.critical("Could not find ZIP URL in API response. Aborting.")
            return False

        logger.info(f"Downloading release from {zip_url}...")
        res = requests.get(zip_url, timeout=60)
        res.raise_for_status()

        zip_file = zipfile.ZipFile(io.BytesIO(res.content))

        # The top-level directory in the zip is usually something like 'user-repo-commit'
        top_level_dir = zip_file.namelist()[0]

        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir)

        logger.info(f"Extracting to temporary directory: {temp_extract_dir}")
        zip_file.extractall(temp_extract_dir)

        update_source_dir = os.path.join(temp_extract_dir, top_level_dir)

        logger.info("Overwriting old files with new version...")
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

        logger.info("File copy complete.")
        return True

    except requests.RequestException as e:
        logger.error(f"A network error occurred during the ZIP update process: {e}")
        return False
    except (zipfile.BadZipFile, OSError) as e:
        logger.error(f"A file or archive error occurred during the ZIP update process: {e}")
        return False
    finally:
        # Cleanup
        if os.path.exists(temp_extract_dir):
            try:
                shutil.rmtree(temp_extract_dir)
            except OSError as e:
                logger.error(f"Failed to clean up temporary update directory: {e}")

def main():
    """
    This script handles the application update process.
    It waits for the main application to shut down, pulls the latest code,
    updates dependencies, and then exits, allowing a service manager to restart the app.
    """
    logger.info("Waiting for main application to close...")
    time.sleep(5)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    logger.info(f"Working directory set to: {project_root}")

    update_succeeded = False
    if os.path.isdir(os.path.join(project_root, ".git")):
        update_succeeded = update_via_git(project_root)
    else:
        update_succeeded = update_via_zip(project_root)

    if not update_succeeded:
        logger.critical("The application was not updated. Please check the errors above.")
        sys.exit(1)

    try:
        logger.info("Installing/updating dependencies...")
        pip_command = [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt']
        subprocess.run(pip_command, check=True, cwd=project_root)
        logger.info("Dependencies are up to date.")

        logger.info("\nUpdate process completed successfully.")
        logger.info("The application will be restarted by systemd or needs to be started manually.")

    except subprocess.CalledProcessError as e:
        logger.error(f"An error occurred during dependency installation: {e}")
        logger.error("Update partially failed. Please run 'pip install -r requirements.txt' manually.")
    except FileNotFoundError:
        logger.error("Could not find 'pip'. Please ensure your Python environment is correctly configured.")
    finally:
        logger.info("Exiting.")

if __name__ == "__main__":
    main()
