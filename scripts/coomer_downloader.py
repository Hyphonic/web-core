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
PLATFORMS = {
    'onlyfans': 'https://coomer.su/api/v1/onlyfans/user',
    'fansly': 'https://coomer.su/api/v1/fansly/user'
}

active_downloads = {}
downloads_lock = threading.Lock()
STOP_MONITOR = threading.Event()

# Add to top with other globals
total_downloaded_bytes = 0
download_bytes_lock = threading.Lock()

def format_size(bytes):
    """Convert bytes to human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024:
            return f"{bytes:.2f}{unit}"
        bytes /= 1024
    return f"{bytes:.2f}TB"

def parse_args():
    parser = argparse.ArgumentParser(description='Coomer.party Downloader')
    parser.add_argument('--debug', action='store_true', default=True, help='Enable debug logging')
    parser.add_argument('--disable-cache', action='store_true', help='Disable cache checking')
    parser.add_argument('--max-urls', type=int, default=MAX_URLS, help='Maximum URLs to download')
    parser.add_argument('--target-posts', type=int, default=50, help='Target posts per creator')
    parser.add_argument('--of-creators', type=str, required=False, help='Comma-separated list of OnlyFans creators')
    parser.add_argument('--fansly-creators', type=str, required=False, help='Comma-separated list of Fansly creators')
    return parser.parse_args()

def anonymize_name(name):
    """Convert creator names to anonymous format (first 2 chars + ****)"""
    return f"{name[:2]}****" if len(name) > 2 else name

def debug_log(msg, show_debug=True):
    if show_debug:
        print(msg)

def get_system_info():
    """Get formatted system information panel"""
    # Disk info
    disk = psutil.disk_usage('cache')
    disk_total_gb = disk.total / (1024**3)
    disk_free_gb = disk.free / (1024**3)
    disk_used_gb = disk.used / (1024**3)
    disk_percent = disk.percent

    # Memory info
    mem = psutil.virtual_memory()
    mem_total_gb = mem.total / (1024**3)
    mem_free_gb = mem.available / (1024**3)
    mem_used_gb = mem_total_gb - mem_free_gb
    mem_percent = mem.percent

    # Add total downloaded size
    global total_downloaded_bytes
    total_downloaded = format_size(total_downloaded_bytes)

    # Active downloads
    with downloads_lock:
        current_tasks = list(active_downloads.items())

    panel = [
        "\n💻 System Status Panel 💻",
        "=" * 50,
        f"📊 Storage Status (cache directory):",
        f"  • Total: {disk_total_gb:.1f}GB",
        f"  • Used:  {disk_used_gb:.1f}GB ({disk_percent}%)",
        f"  • Free:  {disk_free_gb:.1f}GB ({100-disk_percent}%)",
        f"  • Downloaded: {total_downloaded}",
        "",
        f"🧠 Memory Usage:",
        f"  • Total: {mem_total_gb:.1f}GB",
        f"  • Used:  {mem_used_gb:.1f}GB ({mem_percent}%)",
        f"  • Free:  {mem_free_gb:.1f}GB ({100-mem_percent}%)",
        "",
        "🔄 Active Downloads:",
    ]
    
    if not current_tasks:
        panel.append("  • No active downloads.")
    for thread_name, (file_id, started_at) in current_tasks:
        elapsed = (datetime.now() - started_at).seconds
        panel.append(f"  • {thread_name}: {file_id} (running {elapsed}s)")
    
    panel.extend(["", "=" * 50])
    return "\n".join(panel)

def download_file(session, download_url, out_fname, file_id, show_debug=True):
    """Download file using streaming to minimize memory usage"""
    thread_name = threading.current_thread().name
    with downloads_lock:
        active_downloads[thread_name] = (file_id, datetime.now())
    
    try:
        os.makedirs(os.path.dirname(out_fname), exist_ok=True)
        with session.get(download_url, stream=True, timeout=TIMEOUT_SECONDS) as r:
            r.raise_for_status()
            with open(out_fname, "wb") as f:
                bytes_downloaded = 0
                for chunk in r.iter_content(chunk_size=1024*1024):  # 1MB chunks
                    if chunk:
                        bytes_downloaded += len(chunk)
                        f.write(chunk)
                # Update total downloaded bytes
                global total_downloaded_bytes
                with download_bytes_lock:
                    total_downloaded_bytes += bytes_downloaded
        return True
    except Exception as e:
        debug_log(f"  🔴 Error downloading file {file_id}: {e}", show_debug)
        return False
    finally:
        with downloads_lock:
            active_downloads.pop(thread_name, None)

def collect_creator_posts(creator, platform, session, cached_ids, target_posts=50, disable_cache_check=False, show_debug=True):
    collected_posts = {}
    page = 1
    offset = 50
    total_new_posts = 0
    total_pages_checked = 0
    total_checked_posts = 0

    while total_new_posts < target_posts:
        coomer_url = f"{PLATFORMS[platform]}/{creator}?o={offset}"
        debug_log(f"🟢 Fetching {platform} page {page} ({offset}) from {coomer_url}", show_debug)
        
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
                        creator_dir = os.path.join("cache", creator)  # Simplified path
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

def monitor_system():
    """Background thread to display system info every 30 seconds"""
    while not STOP_MONITOR.is_set():
        debug_log(get_system_info(), True)
        time.sleep(30)

def main():
    args = parse_args()
    
    # Start system monitor thread
    monitor_thread = threading.Thread(target=monitor_system)
    monitor_thread.daemon = True
    monitor_thread.start()
    
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

    # Stop monitor thread
    STOP_MONITOR.set()
    monitor_thread.join(timeout=1)

if __name__ == "__main__":
    main()
