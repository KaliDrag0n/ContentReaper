# lib/sanitizer.py
import re
import unicodedata

def sanitize_filename(name):
    """
    Sanitizes a string to be a safe filename or folder name component.
    - Prevents path traversal attacks (e.g., '../', '/').
    - Normalizes unicode characters for consistency.
    - Replaces characters that are illegal in Windows, macOS, and Linux filenames.
    - Removes leading/trailing whitespace and periods.
    - Ensures the name is not empty after sanitization.
    """
    if not isinstance(name, str) or not name:
        return ""
    
    # --- CHANGE: Normalize unicode characters for consistency without removing them. ---
    # This keeps characters like 'é', 'ü', 'ñ', etc., which are valid in modern filesystems.
    safe_name = unicodedata.normalize('NFC', name)
    
    # --- CHANGE: Replace only truly invalid filesystem characters. ---
    # This regex targets characters illegal on Windows, which is the strictest subset.
    # It also removes control characters (ASCII 0-31).
    safe_name = re.sub(r'[\x00-\x1f\\/?*:"<>|]', '-', safe_name)
    
    # Collapse consecutive whitespace characters into a single space.
    safe_name = re.sub(r'\s+', ' ', safe_name)
    
    # Remove leading/trailing whitespace and periods, which can cause issues.
    safe_name = safe_name.strip().strip('.')
    
    # If the name is empty after all sanitization (e.g., input was just "."),
    # return a default name.
    if not safe_name:
        return "Untitled"
        
    return safe_name

