# lib/sanitizer.py
import re
import unicodedata
import os

# A set of reserved filenames for Windows. These are case-insensitive.
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}

# --- CHANGE: Added a constant for the maximum filename length ---
# We use 240 as a safe limit to leave room for extensions and filesystem variations.
MAX_FILENAME_LENGTH = 240

def sanitize_filename(name: str) -> str:
    """
    Sanitizes a string to be a safe filename or folder name.
    - Prevents path traversal attacks.
    - Normalizes unicode characters.
    - Replaces illegal characters for Windows, macOS, and Linux.
    - Rejects reserved Windows names.
    - Truncates names that are too long.
    - Removes leading/trailing whitespace and periods.
    - Ensures the name is not empty.
    
    Args:
        name (str): The original filename or component.

    Returns:
        str: A sanitized, safe-to-use filename string.
    """
    if not isinstance(name, str) or not name:
        return "Untitled"
    
    # 1. Normalize unicode characters for cross-platform consistency.
    # 'NFC' is a good standard that avoids breaking down characters unnecessarily.
    safe_name = unicodedata.normalize('NFC', name)
    
    # 2. Replace illegal characters and collapse consecutive replacements.
    # This regex targets characters illegal on Windows (the strictest set)
    # and ASCII control characters (0-31).
    safe_name = re.sub(r'[\x00-\x1f\\/?*:"<>|]+', '-', safe_name)
    
    # 3. Collapse consecutive whitespace characters into a single space.
    safe_name = re.sub(r'\s+', ' ', safe_name).strip()
    
    # 4. Remove leading/trailing periods, which can cause issues on some systems.
    safe_name = safe_name.strip('.')
    
    # 5. Check against reserved Windows filenames.
    # We check the name without extension, as "CON.txt" is also invalid.
    name_without_ext, dot, extension = safe_name.rpartition('.')
    # If there's no dot, the whole name is checked.
    check_name = name_without_ext if dot else safe_name
    
    if check_name.upper() in WINDOWS_RESERVED_NAMES:
        # Prepend an underscore if the name is reserved.
        safe_name = f"_{safe_name}"

    # --- CHANGE: Truncate filename if it's too long, preserving the extension ---
    if len(safe_name.encode('utf-8')) > MAX_FILENAME_LENGTH:
        # Re-evaluate the name/extension split after the reserved name check.
        name_without_ext, dot, extension = safe_name.rpartition('.')
        
        if dot: # If an extension exists
            # Calculate how much we can keep of the name part.
            max_name_len = MAX_FILENAME_LENGTH - len(dot.encode('utf-8')) - len(extension.encode('utf-8'))
            
            # Truncate the name part by slicing its byte representation.
            truncated_name = name_without_ext.encode('utf-8')[:max_name_len].decode('utf-8', 'ignore')
            safe_name = truncated_name + dot + extension
        else: # No extension
            safe_name = safe_name.encode('utf-8')[:MAX_FILENAME_LENGTH].decode('utf-8', 'ignore')

    # 6. If the name is empty after all sanitization, return a default.
    if not safe_name.strip():
        return "Untitled"
        
    return safe_name
