# lib/sanitizer.py
import re
import unicodedata

def sanitize_filename(name):
    """
    Sanitizes a string to be a safe filename or folder name component.
    - Prevents path traversal attacks (e.g., '../', '/').
    - Normalizes unicode characters.
    - Replaces invalid filesystem characters for Windows, macOS, and Linux.
    - Removes leading/trailing whitespace and periods.
    - Ensures the name is not empty after sanitization.
    """
    # --- FIX: Return an empty string for empty/invalid input ---
    # This allows downstream logic to decide on a default name like "Untitled" or the video title.
    if not isinstance(name, str) or not name:
        return ""
    
    # Normalize unicode characters to their closest ASCII representation.
    safe_name = unicodedata.normalize('NFKC', name)
    safe_name = safe_name.encode('ascii', 'ignore').decode('ascii')
    
    # --- SECURITY: Replace path separators and other invalid characters ---
    safe_name = re.sub(r'[\\/?*:"<>|]', '-', safe_name)
    
    # Collapse consecutive whitespace characters into a single space
    safe_name = re.sub(r'\s+', ' ', safe_name)
    
    # Remove leading/trailing whitespace and periods.
    safe_name = safe_name.strip().strip('.')
    
    # --- FIX: Only return "Untitled" if the name is empty AFTER sanitization ---
    # This handles cases where the input was something like "..", which becomes empty.
    if not safe_name:
        return "Untitled"
        
    return safe_name
