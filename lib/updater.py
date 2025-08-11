import os
import subprocess
import sys
import time
import requests

GITHUB_REPO_SLUG = "KaliDrag0n/Downloader-Web-UI"

def main():
    """
    This script handles the application update process.
    It waits for the main application to shut down, pulls the latest code from git,
    updates dependencies, and then exits, allowing a service manager to restart the app.
    This script is designed to be run as a detached process.
    """
    print("Updater: Waiting for main application to close...")
    time.sleep(5)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    print(f"Updater: Working directory set to: {project_root}")

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
            sys.exit(1)

        if latest_tag:
            print(f"Updater: Found latest release: {latest_tag}. Checking it out...")
            subprocess.run(["git", "fetch", "--tags", "--force"], check=True, cwd=project_root)
            subprocess.run(["git", "checkout", latest_tag], check=True, cwd=project_root)
            print(f"Updater: Successfully checked out release {latest_tag}.")
        else:
            print("Updater: CRITICAL: No release tag found in API response. Aborting update.")
            sys.exit(1)

        print("Updater: Installing/updating dependencies...")
        pip_command = [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt']
        subprocess.run(pip_command, check=True, cwd=project_root)
        print("Updater: Dependencies are up to date.")

        print("\nUpdater: Update process completed successfully.")
        print("Updater: The application will be restarted by systemd or needs to be started manually.")

    except subprocess.CalledProcessError as e:
        print(f"Updater: An error occurred during the update process: {e}")
        print("Updater: The application was not updated. Please check the errors above.")
    except FileNotFoundError as e:
        print(f"Updater: A command was not found. Is git installed and in the system's PATH? Error: {e}")
        print("Updater: Could not perform update. Please ensure git is installed.")
    finally:
        print("Updater: Exiting.")

if __name__ == "__main__":
    main()