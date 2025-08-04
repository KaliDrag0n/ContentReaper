# lib/sanitizer.py
import re
import unicodedata

def sanitize_filename(name):
    """
    Sanitizes a string to be a safe filename.
    - Normalizes unicode characters.
    - Replaces invalid filesystem characters.
    - Removes leading/trailing whitespace and periods.
    """
    if not name:
        return ""
    
    # Normalize unicode characters to their closest ASCII representation
    safe_name = unicodedata.normalize('NFKC', name)
    safe_name = safe_name.encode('ascii', 'ignore').decode('ascii')
    
    # Replace invalid characters with a hyphen
    safe_name = re.sub(r'[\\/*?:"<>|]', '-', safe_name)
    
    # Replace colons, which are often used in titles but invalid in Windows filenames
    safe_name = safe_name.replace(':', '-')

    # Collapse consecutive whitespace characters into a single space
    safe_name = re.sub(r'\s+', ' ', safe_name)
    
    # Remove leading/trailing whitespace and periods
    safe_name = safe_name.strip().strip('.')
    
    # If the name is empty after sanitization, provide a default
    if not safe_name:
        return "Untitled"
        
    return safe_name
