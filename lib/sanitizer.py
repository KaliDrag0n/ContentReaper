# lib/sanitizer.py
import re
import unicodedata

# --- CHANGE: Added a set of reserved filenames for Windows ---
# These names are invalid as the main name of a file, regardless of extension.
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}

def sanitize_filename(name: str) -> str:
    """
    Sanitizes a string to be a safe filename or folder name component.
    - Prevents path traversal attacks (e.g., '../', '/').
    - Normalizes unicode characters for consistency.
    - Replaces characters that are illegal in Windows, macOS, and Linux filenames.
    - Rejects names that are reserved on Windows.
    - Removes leading/trailing whitespace and periods.
    - Ensures the name is not empty after sanitization.
    
    Args:
        name (str): The original filename or component.

    Returns:
        str: A sanitized, safe-to-use filename string.
    """
    if not isinstance(name, str) or not name:
        return ""
    
    # 1. Normalize unicode characters for cross-platform consistency.
    # 'NFC' is a good standard that avoids breaking down characters unnecessarily.
    safe_name = unicodedata.normalize('NFC', name)
    
    # 2. Replace illegal characters.
    # This regex targets characters illegal on Windows (the strictest set)
    # and control characters (ASCII 0-31).
    safe_name = re.sub(r'[\x00-\x1f\\/?*:"<>|]', '-', safe_name)
    
    # 3. Collapse consecutive whitespace characters into a single space.
    safe_name = re.sub(r'\s+', ' ', safe_name)
    
    # 4. Remove leading/trailing whitespace and periods, which can cause issues.
    safe_name = safe_name.strip(' .')
    
    # --- CHANGE: Check against reserved Windows filenames ---
    # We check the name without extension.
    name_without_ext, _, _ = safe_name.rpartition('.')
    if name_without_ext.upper() in WINDOWS_RESERVED_NAMES:
        # Prepend an underscore if the name is reserved.
        safe_name = f"_{safe_name}"

    # 5. If the name is empty after all sanitization (e.g., input was just "."),
    # return a default name to prevent errors.
    if not safe_name:
        return "Untitled"
        
    return safe_name
