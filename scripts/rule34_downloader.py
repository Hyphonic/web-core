import os
import json
import shutil
import requests
import concurrent.futures
import argparse
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import psutil
import threading
from datetime import datetime
import time

# Constants and Configuration
TIMEOUT_SECONDS = 300  # 5 minutes
MAX_WORKERS = 8
MAX_URLS = 250
MIN_DISK_SPACE = 2 * 1024 * 1024 * 1024  # 2GB in bytes
BASE_URL = 'https://api.rule34.xxx/index.php'

active_downloads = {}
downloads_lock = threading.Lock()

total_downloaded_bytes = 0
download_bytes_lock = threading.Lock()

last_bytes_check = 0
last_check_time = time.time()
current_speed = 0  # bytes per second
speed_lock = threading.Lock()

def parse_args():
    parser = argparse.ArgumentParser(description='Rule34 Downloader')
    parser.add_argument('--debug', action='store_true', default=True, help='Enable debug logging')
    parser.add_argument('--disable-cache', action='store_true', help='Disable cache checking')
    parser.add_argument('--max-urls', type=int, default=MAX_URLS, help='Maximum URLs to download')
    parser.add_argument('--target-posts', type=int, default=50, help='Target posts per creator')
    parser.add_argument('--creators', type=str, required=False, help='Comma-separated list of creator tags')
    return parser.parse_args()

def collect_creator_posts(creator, session, cached_ids, target_posts=50, disable_cache_check=False, show_debug=True):
    collected_posts = {}
    page = 0  # Rule34 starts at page 0
    total_new_posts = 0
    total_pages_checked = 0
    total_checked_posts = 0

    while total_new_posts < target_posts:
        # Rule34 API params
        params = {
            'page': 'dapi',
            's': 'post',
            'q': 'index',
            'json': '1',
            'tags': creator,
            'pid': page  # pid = page number (0-based)
        }
        
        try:
            resp = session.get(BASE_URL, params=params, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            items = resp.json()
            
            if not items:
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
                file_url = item.get('url')
                
                if file_url:
                    creator_dir = os.path.join("cache", creator)
                    file_ext = os.path.splitext(file_url)[1]
                    out_fname = os.path.join(creator_dir, f"{file_id}{file_ext}")
                    collected_posts[file_id] = [(file_url, out_fname)]
                    total_new_posts += 1
                        
                if total_new_posts >= target_posts:
                    break
            
            debug_log(f"  📄 Page {page}: Found {page_stats['new']} new posts, skipped {page_stats['cached']} cached posts", show_debug)
            page += 1  # Move to next page
            
        except Exception as e:
            debug_log(f"🔴 Failed to fetch page {page} for {creator}: {e}", show_debug)
            break
    
    return collected_posts

def check_disk_space(path="."):
    """Check if enough disk space is available."""
    total, used, free = shutil.disk_usage(path)
    return free > MIN_DISK_SPACE, free / (5 * 1024 * 1024)  # Return bool and GB free

def display_download_preview(unique_tasks, cached_ids, show_debug=True):
    """Display preview of upcoming downloads."""
    if not show_debug:
        return

    print("\n📊 Download Preview:")
    print("=" * 50)
    
    # Group by creator and count files
    creator_stats = {}
    creator_posts = {}
    for (url, fname), file_id in unique_tasks.items():
        creator = os.path.basename(os.path.dirname(fname))  # Path already simplified
        creator_stats[creator] = creator_stats.get(creator, 0) + 1
        if creator not in creator_posts:
            creator_posts[creator] = set()
        creator_posts[creator].add(file_id)

    print("\n👤 Per Creator Breakdown:")
    print("-" * 50)
    for creator in creator_stats:
        files = creator_stats[creator]
        posts = len(creator_posts[creator])
        ratio = files / posts if posts > 0 else 0
        print(f"  • {anonymize_name(creator)}:")
        print(f"    - Files to download: {files}")
        print(f"    - Unique posts: {posts}")
        print(f"    - Files per post: {ratio:.1f}")
    
    print("\n📈 Preview Totals:")
    print("-" * 50)
    total_files = sum(creator_stats.values())
    total_posts = sum(len(posts) for posts in creator_posts.values())
    print(f"  • Total files to download: {total_files}")
    print(f"  • Total unique posts: {total_posts}")
    print(f"  • Current cache size: {len(cached_ids)}")
    print("=" * 50 + "\n")

def display_download_results(unique_tasks, cached_ids, successful_downloads, successful_ids, show_debug=True):
    """Display final download results."""
    if not show_debug:
        return

    print("\n📊 Download Results:")
    print("=" * 50)
    
    # Group results by creator
    creator_stats = {}
    for (url, fname), file_id in unique_tasks.items():
        creator = os.path.basename(os.path.dirname(fname))  # Path already simplified
        if creator not in creator_stats:
            creator_stats[creator] = {'total': 0, 'success': 0, 'posts': set()}
        creator_stats[creator]['total'] += 1
        if file_id in successful_ids:
            creator_stats[creator]['success'] += 1
            creator_stats[creator]['posts'].add(file_id)

    print("\n👤 Per Creator Results:")
    print("-" * 50)
    for creator, stats in creator_stats.items():
        success_rate = (stats['success'] / stats['total'] * 100) if stats['total'] > 0 else 0
        print(f"  • {anonymize_name(creator)}:")
        print(f"    - Successfully downloaded: {stats['success']}/{stats['total']} files ({success_rate:.1f}%)")
        print(f"    - Unique posts added: {len(stats['posts'])}")
        if stats['posts']:
            ratio = stats['success'] / len(stats['posts'])
            print(f"    - Files per post: {ratio:.1f}")

    print("\n📈 Final Totals:")
    print("-" * 50)
    print(f"  • Total files downloaded: {successful_downloads}")
    print(f"  • New posts added to cache: {len(successful_ids)}")
    print(f"  • Total cache size: {len(cached_ids)}")
    print("=" * 50 + "\n")

def main():
    args = parse_args()
    
    # Debug creator lists
    of_creators = []
    fansly_creators = []
    
    if args.of_creators:
        of_creators = [c.strip() for c in args.of_creators.split(',') if c.strip()]
        debug_log(f"🔵 Found {len(of_creators)} OnlyFans creators: {[anonymize_name(c) for c in of_creators]}", args.debug)
    
    if args.fansly_creators:
        fansly_creators = [c.strip() for c in args.fansly_creators.split(',') if c.strip()]
        debug_log(f"🔵 Found {len(fansly_creators)} Fansly creators: {[anonymize_name(c) for c in fansly_creators]}", args.debug)
    
    if not of_creators and not fansly_creators:
        debug_log("🔴 No creators specified for any platform!", args.debug)
        return

    cache_file = "cache/rule34_ids.json"
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
        debug_log("🟢 Loaded cached Rule34 IDs.", args.debug)
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

    # Collect posts from all creators across platforms
    for platform, creators in [('onlyfans', of_creators), ('fansly', fansly_creators)]:
        if not creators:
            debug_log(f"🔵 Skipping {platform} - no creators specified", args.debug)
            continue
            
        for creator in creators:
                
            debug_log(f"🟢 Processing {platform} creator: {anonymize_name(creator)}", args.debug)
            creator_posts = collect_creator_posts(
                creator, platform, session, cached_ids,
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
            # Check disk space and show system panel every 50 downloads
            if completed % 50 == 0:
                debug_log(get_system_info(), args.debug)
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