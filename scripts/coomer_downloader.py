import os
import json
import shutil
import requests
import concurrent.futures
import argparse
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# Constants and Configuration
TIMEOUT_SECONDS = 300  # 5 minutes
MAX_WORKERS = 8
MAX_URLS = 150
CREATORS = [
    "darcyxnycole", "belledelphine", "sweetiefox_of", "soogsx",
    "morgpie", "w0llip", "summertimejames", "f1nn5ter",
    "jamelizzzz", "lilijunex", "lunluns"
]
MIN_DISK_SPACE = 2 * 1024 * 1024 * 1024  # 2GB in bytes

def parse_args():
    parser = argparse.ArgumentParser(description='Coomer.party Downloader')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--disable-cache', action='store_true', help='Disable cache checking')
    parser.add_argument('--max-urls', type=int, default=MAX_URLS, help='Maximum URLs to download')
    parser.add_argument('--target-posts', type=int, default=50, help='Target posts per creator')
    return parser.parse_args()

def debug_log(msg, show_debug=True):
    if show_debug:
        print(msg)

def download_file(session, download_url, out_fname, file_id, show_debug=True):
    try:
        r = session.get(download_url, timeout=TIMEOUT_SECONDS)
        r.raise_for_status()
        os.makedirs(os.path.dirname(out_fname), exist_ok=True)
        with open(out_fname, "wb") as out:
            out.write(r.content)
        return True
    except Exception as e:
        debug_log(f"  🔴 Error downloading file {file_id}: {e}", show_debug)
        return False

def collect_creator_posts(creator, session, cached_ids, target_posts=50, disable_cache_check=False, show_debug=True):
    collected_posts = {}  # {file_id: [(url, filename), ...]}
    page = 1
    offset = 50  # Starting offset
    total_new_posts = 0
    total_pages_checked = 0
    total_checked_posts = 0

    while total_new_posts < target_posts:
        coomer_url = f"https://coomer.su/api/v1/onlyfans/user/{creator}?o={offset}"
        debug_log(f"🟢 Fetching page {page} ({offset}) from {coomer_url}", show_debug)
        
        try:
            resp = session.get(coomer_url, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            items = resp.json()
            
            if not items:  # Truly no more posts available
                debug_log(f"🔵 Reached end of available posts for {creator} after {total_pages_checked} pages", show_debug)
                break
                
            total_pages_checked += 1
            total_checked_posts += len(items)
            page_stats = {'new': 0, 'cached': 0, 'total': len(items)}
            
            for item in items:
                file_id = str(item.get('id', ''))
                if file_id in cached_ids and not disable_cache_check:
                    page_stats['cached'] += 1
                    continue
                    
                page_stats['new'] += 1
                paths = set()
                if 'file' in item and 'path' in item['file']:
                    paths.add(item['file']['path'])
                for att in item.get('attachments', []):
                    p = att.get('path')
                    if p:
                        paths.add(p)
                
                if paths:  # Only count posts with media
                    collected_posts[file_id] = []
                    for p in paths:
                        download_url = "https://coomer.su" + p
                        creator_dir = os.path.join("cache", creator)
                        out_fname = os.path.join(creator_dir, f"{file_id}-{os.path.basename(p)}")
                        collected_posts[file_id].append((download_url, out_fname))
                        total_new_posts += 1
                        
                if total_new_posts >= target_posts:
                    break
            
            debug_log(f"  📄 Page {page}: Found {page_stats['new']} new posts, skipped {page_stats['cached']} cached posts", show_debug)
                
            page += 1
            offset += 50
            
        except Exception as e:
            debug_log(f"🔴 Failed to fetch page {page} for {creator}: {e}", show_debug)
            break
    
    debug_log(f"📊 Creator {creator} summary:", show_debug)
    debug_log(f"  • Pages checked: {total_pages_checked}", show_debug)
    debug_log(f"  • Posts checked: {total_checked_posts}", show_debug)
    debug_log(f"  • New posts found: {len(collected_posts)}", show_debug)
    
    return collected_posts

def check_disk_space(path="."):
    """Check if enough disk space is available."""
    total, used, free = shutil.disk_usage(path)
    return free > MIN_DISK_SPACE, free / (5 * 1024 * 1024)  # Return bool and GB free

def main():
    args = parse_args()
    cache_file = "cache/coomer_ids.json"
    os.makedirs("cache", exist_ok=True)

    # Check disk space before starting
    has_space, gb_free = check_disk_space("cache")
    if not has_space:
        debug_log(f"🔴 Not enough disk space! Only {gb_free:.1f}GB free. Need at least 5GB.", args.debug)
        return

    # Load cache
    try:
        with open(cache_file, "r") as f:
            cached_ids = set(json.load(f))
        debug_log("🟢 Loaded cached Coomer IDs.", args.debug)
    except:
        cached_ids = set()
        debug_log("🔴 No cache found. Starting fresh.", args.debug)

    # Setup session
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    unique_tasks = {}
    successful_ids = set()
    successful_downloads = 0

    # Collect posts from all creators
    for creator in CREATORS:
        debug_log(f"🟢 Processing creator: {creator}", args.debug)
        creator_posts = collect_creator_posts(
            creator, session, cached_ids,
            args.target_posts, args.disable_cache, args.debug
        )
        
        # Add tasks from this creator
        for file_id, urls_and_fnames in creator_posts.items():
            for download_url, out_fname in urls_and_fnames:
                if len(unique_tasks) >= args.max_urls:
                    break
                unique_tasks[(download_url, out_fname)] = file_id
        
        if len(unique_tasks) >= args.max_urls:
            debug_log(f"🟢 Reached maximum URL limit of {args.max_urls}", args.debug)
            break

    # Convert tasks for parallel download
    tasks = [(k[0], k[1], v) for k, v in unique_tasks.items()]
    total_tasks = len(tasks)
    debug_log(f"🟢 Starting parallel downloads for {total_tasks} unique files.", args.debug)
    completed = 0

    # Display download preview
    display_download_preview(unique_tasks, cached_ids, args.debug)

    # Download files in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            executor.submit(
                download_file, session, url, fname, fid, args.debug
            ): (url, fname, fid)
            for (url, fname, fid) in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            # Check disk space every 10 downloads
            if completed % 10 == 0:
                has_space, gb_free = check_disk_space("cache")
                if not has_space:
                    debug_log(f"🔴 Stopping downloads - Only {gb_free:.1f}GB free space left!", args.debug)
                    break

            url, fname, fid = future_map[future]
            success = future.result()
            completed += 1
            if success:
                successful_downloads += 1
                successful_ids.add(fid)
                try:
                    percentage = round((completed / total_tasks) * 100)
                    debug_log(f"  🟡 ({completed}/{total_tasks}) [{percentage}%] Downloaded {fid} -> {fname}", args.debug)
                except:
                    debug_log(f"  🟡 ({completed}/{total_tasks}) Downloaded {fid} -> {fname}", args.debug)
            else:
                debug_log(f"  🔴 ({completed}/{total_tasks})  Failed {fid}", args.debug)

    # Display final statistics
    display_download_results(unique_tasks, cached_ids, successful_downloads, successful_ids, args.debug)

    # Update cache with successful downloads
    if successful_ids:
        cached_ids.update(successful_ids)
        with open(cache_file, "w") as f:
            json.dump(sorted(cached_ids), f)
        debug_log(f"🟢 Added {len(successful_ids)} new posts ({successful_downloads} files) to cache.", args.debug)
    else:
        debug_log("🟠 No New Items Found!", args.debug)

if __name__ == "__main__":
    main()
