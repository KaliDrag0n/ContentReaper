import os
import subprocess
import sys
import time

def main():
    """
    This script handles the application update process.
    It waits for the main application to shut down, pulls the latest code from git,
    updates dependencies, and then restarts the application.
    This script is designed to be run as a detached process.
    """
    # Wait for 5 seconds to ensure the main application has fully shut down
    # and released all file handles and the network port.
    print("Updater: Waiting for main application to close...")
    time.sleep(5)

    # The root directory of the project is one level above this script's location
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    print(f"Updater: Working directory set to: {project_root}")

    try:
        # Step 1: Update the code from the git repository.
        # This will now attempt to check out the latest release tag.
        print("Updater: Fetching latest releases from git...")
        # Fetch all tags from the remote, ensuring we have the latest release info.
        subprocess.run(["git", "fetch", "--tags", "--force"], check=True, cwd=project_root)
        
        # Get a list of all tags, sorted by version number (descending).
        get_tags_command = ["git", "tag", "-l", "--sort=-v:refname"]
        tag_result = subprocess.run(get_tags_command, check=True, cwd=project_root, capture_output=True, text=True)
        tags = tag_result.stdout.strip().splitlines()

        if tags:
            # If tags exist, check out the latest one.
            latest_tag = tags[0]
            print(f"Updater: Found latest release: {latest_tag}. Checking it out...")
            subprocess.run(["git", "checkout", latest_tag], check=True, cwd=project_root)
            print(f"Updater: Successfully checked out release {latest_tag}.")
        else:
            # Fallback for repositories with no tags.
            print("Updater: No release tags found. Performing a standard 'git pull'...")
            subprocess.run(["git", "pull"], check=True, cwd=project_root)
            print("Updater: Git pull completed successfully.")

        # Step 2: Install or update any changed dependencies from requirements.txt.
        # We use sys.executable to ensure we're using the pip from the correct
        # Python virtual environment (if one is active).
        print("Updater: Installing/updating dependencies...")
        pip_command = [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt']
        subprocess.run(pip_command, check=True, cwd=project_root)
        print("Updater: Dependencies are up to date.")

        # Step 3: Relaunch the main application.
        # We use Popen to launch it as a new, independent process.
        print("Updater: Relaunching the main application...")
        main_script_path = os.path.join(project_root, 'web_tool.py')
        subprocess.Popen([sys.executable, main_script_path], cwd=project_root)
        print("Updater: Relaunch command issued. The updater will now exit.")

    except subprocess.CalledProcessError as e:
        print(f"Updater: An error occurred during the update process: {e}")
        print("Updater: Attempting to restart the application with the old version...")
        main_script_path = os.path.join(project_root, 'web_tool.py')
        subprocess.Popen([sys.executable, main_script_path], cwd=project_root)

    except FileNotFoundError as e:
        print(f"Updater: A command was not found. Is git installed and in the system's PATH? Error: {e}")
        print("Updater: Could not perform update. Please ensure git is installed.")

if __name__ == "__main__":
    main()