# get_slug.py
import sys
from urllib.parse import urlparse
import re

def create_slug(url):
    try:
        if not url: return "error_no_url"
        parsed_url = urlparse(url)
        path_parts = [part for part in parsed_url.path.strip('/').split('/') if part]
        
        slug_base = ""
        if len(path_parts) >= 2 and path_parts[0].startswith('@'):
            author = path_parts[0][1:]
            project_name = path_parts[1]
            slug_base = f"{author}_{project_name}"
        elif len(path_parts) > 0:
            slug_base = "_".join(path_parts)
        else:
            domain = parsed_url.netloc.lower().replace('www.', '')
            slug_base = domain.split('.')[0]
        
        slug = re.sub(r'[^a-zA-Z0-9\-_]', '-', slug_base)
        slug = re.sub(r'[-_]+', '-', slug)
        slug = slug.strip('-')
        return slug[:70]
    except Exception as e:
        return f"error_{e}"

if __name__ == "__main__":
    print("--- CÔNG CỤ LẤY SLUG DỰ ÁN ---")
    while True:
        url = input("\nNhập URL game (hoặc 'q' để thoát): ").strip()
        if url.lower() == 'q': break
        print(f"👉 Tên file JSON cần đặt: {create_slug(url)}.json")