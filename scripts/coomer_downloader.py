import os
import json
import requests
import concurrent.futures
import argparse
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# Constants and Configuration
TIMEOUT_SECONDS = 300  # 5 minutes
MAX_WORKERS = 8
MAX_URLS = 250
CREATORS = [
    "darcyxnycole", "belledelphine", "sweetiefox_of", "soogsx",
    "morgpie", "w0llip", "summertimejames", "f1nn5ter",
    "jamelizzzz", "lilijunex", "lunluns"
]

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

    while total_new_posts < target_posts:
        coomer_url = f"https://coomer.su/api/v1/onlyfans/user/{creator}?o={offset}"
        debug_log(f"🟢 Fetching page {page} ({offset}) from {coomer_url}", show_debug)
        
        try:
            resp = session.get(coomer_url, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            items = resp.json()
            
            if not items:  # No more posts available
                debug_log(f"🔵 No more posts found for {creator}", show_debug)
                break
                
            new_items_found = False
            for item in items:
                file_id = str(item.get('id', ''))
                if file_id in cached_ids and not disable_cache_check:
                    debug_log(f"  🔵 Skipping cached file: {file_id}", show_debug)
                    continue
                    
                new_items_found = True
                debug_log(f"  🟡 Found new post: {file_id}", show_debug)
                
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
            
            if not new_items_found:
                debug_log(f"🔵 No new posts found on page {page} for {creator}", show_debug)
                break
                
            page += 1
            offset += 50
            
        except Exception as e:
            debug_log(f"🔴 Failed to fetch page {page} for {creator}: {e}", show_debug)
            break
    
    return collected_posts

def display_download_stats(creators_data, unique_tasks, cached_ids, show_debug=True):
    """Display statistics about the upcoming download."""
    if not show_debug:
        return

    print("\n📊 Download Statistics:")
    print("=" * 50)
    print(f"💾 Cached posts: {len(cached_ids)}")
    
    creator_stats = {}
    for (url, fname), file_id in unique_tasks.items():
        creator = os.path.basename(os.path.dirname(fname))
        creator_stats[creator] = creator_stats.get(creator, 0) + 1

    print("\n👤 Per Creator Breakdown:")
    print("-" * 50)
    for creator, count in creator_stats.items():
        print(f"  • {creator}: {count} files")
    
    print("\n📈 Totals:")
    print("-" * 50)
    print(f"  • Creators to process: {len(creator_stats)}")
    print(f"  • Total URLs to download: {len(unique_tasks)}")
    print("=" * 50 + "\n")

def main():
    args = parse_args()
    cache_file = "cache/coomer_ids.json"
    os.makedirs("cache", exist_ok=True)

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

    # Display download statistics
    display_download_stats(CREATORS, unique_tasks, cached_ids, args.debug)

    # Convert tasks for parallel download
    tasks = [(k[0], k[1], v) for k, v in unique_tasks.items()]
    total_tasks = len(tasks)
    debug_log(f"🟢 Starting parallel downloads for {total_tasks} unique files.", args.debug)
    completed = 0

    # Download files in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            executor.submit(
                download_file, session, url, fname, fid, args.debug
            ): (url, fname, fid)
            for (url, fname, fid) in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            url, fname, fid = future_map[future]
            success = future.result()
            completed += 1
            percent = int((completed / total_tasks) * 100)
            if success:
                successful_ids.add(fid)
                debug_log(f"  🟡 ({percent}%) Downloaded {fid} -> {fname}", args.debug)
            else:
                debug_log(f"  🔴 ({percent}%) Failed {fid}", args.debug)

    # Update cache with successful downloads
    if successful_ids:
        cached_ids.update(successful_ids)
        with open(cache_file, "w") as f:
            json.dump(sorted(cached_ids), f)
        debug_log(f"🟢 Downloaded and cached {len(successful_ids)} new Coomer files.", args.debug)
    else:
        debug_log("🟠 No New Items Found!", args.debug)

if __name__ == "__main__":
    main()
