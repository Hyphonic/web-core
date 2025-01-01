import os
import json
import requests
import praw
import subprocess
import sys
import argparse
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# Constants
MEME_LIMIT = 250
VALID_IMAGE_EXTS = ['.png','.jpg','.jpeg','.webp','.gif']
SUBREDDITS = [
    "Memes", "ProgrammerHumor", "DankMemes", "DirtyMemes", 
    "RareInsults", "Funny", "Science", "TodayILearned", 
    "MemeVideos", "MeIRL", "Gifs", "Aww", "Videos", 
    "AskReddit", "HolUp", "WTF", "Hmmm", "CoolGuides", 
    "Unexpected", "SweatyPalms", "SpreadSmile", "Pranks"
]

def parse_args():
    parser = argparse.ArgumentParser(description='Reddit Meme Downloader')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--disable-cache', action='store_true', help='Disable cache checking')
    parser.add_argument('--post-limit', type=int, default=5, help='Posts to fetch per subreddit')
    parser.add_argument('--client-id', required=True, help='Reddit Client ID')
    parser.add_argument('--client-secret', required=True, help='Reddit Client Secret')
    parser.add_argument('--user-agent', required=True, help='Reddit User Agent')
    return parser.parse_args()

def debug_log(msg, show_debug=True):
    if show_debug:
        print(msg)

def setup_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def process_image_post(post, session, memes_metadata, new_ids, show_debug=True):
    try:
        ext = os.path.splitext(post.url.lower())[1]
        if ext not in VALID_IMAGE_EXTS:
            return False

        debug_log(f"  🟡 Found image post: {post.id}", show_debug)
        r = session.get(post.url, timeout=10)
        r.raise_for_status()
        out_fname = os.path.join("cache", f"{post.id}{ext}")
        
        with open(out_fname, "wb") as f:
            f.write(r.content)
        
        memes_metadata.append({
            "id": post.id,
            "title": post.title,
            "author": str(post.author) if post.author else "Unknown",
            "subreddit": str(post.subreddit),
            "upvotes": post.ups,
            "filename": os.path.basename(out_fname),
            "type": "image"
        })
        new_ids.append(post.id)
        debug_log(f"    🟡 Downloaded image: {out_fname}", show_debug)
        return True
    except Exception as e:
        debug_log(f"  🔴 Error processing post {post.id}: {e}", show_debug)
        return False

def process_video_post(post, memes_metadata, new_ids, show_debug=True):
    try:
        outtmpl = f"cache/{post.id}.%(ext)s"
        with open(os.devnull, 'w') as devnull:
            result = subprocess.run([
                sys.executable, '-m', 'yt_dlp', '--quiet', '--no-warnings', 
                '--ignore-errors', '--format', 'best', 
                '--merge-output-format', 'mp4', '--output', outtmpl, 
                post.url
            ], stdout=devnull, stderr=devnull)

        downloaded_file = None
        for file in os.listdir("cache"):
            if file.startswith(post.id):
                downloaded_file = os.path.join("cache", file)
                break

        if downloaded_file:
            debug_log(f"  🟡 Downloaded video: {downloaded_file}", show_debug)
            memes_metadata.append({
                "id": post.id,
                "title": post.title,
                "author": str(post.author) if post.author else "Unknown",
                "subreddit": str(post.subreddit),
                "upvotes": post.ups,
                "filename": os.path.basename(downloaded_file),
                "type": "video"
            })
            new_ids.append(post.id)
            return True
    except Exception as e:
        debug_log(f"  🔴 Error processing video post {post.id}: {e}", show_debug)
    return False

def main():
    args = parse_args()
    os.makedirs("cache", exist_ok=True)
    
    # Initialize Reddit API
    reddit = praw.Reddit(
        client_id=args.client_id,
        client_secret=args.client_secret,
        user_agent=args.user_agent,
    )
    debug_log("🟢 Reddit instance created.", args.debug)
    
    # Load cache
    cache_file = "cache/meme_ids.json"
    try:
        with open(cache_file, "r") as f:
            cached_ids = set(json.load(f))
        debug_log("🟢 Loaded cached Meme IDs.", args.debug)
    except:
        cached_ids = set()
        debug_log("🔴 No cache found. Starting fresh.", args.debug)

    session = setup_session()
    total_memes = 0
    new_ids = []
    memes_metadata = []
    video_posts = []

    # Process subreddits
    for subreddit in SUBREDDITS:
        if total_memes >= MEME_LIMIT:
            debug_log("🔴 Meme limit reached. Stopping download.", args.debug)
            break
            
        debug_log(f"🟢 Scraping r/{subreddit} for new posts...", args.debug)
        for post in reddit.subreddit(subreddit).new(limit=args.post_limit):
            if not args.disable_cache and post.id in cached_ids:
                debug_log(f"  🔵 Skipping cached post: {post.id}", args.debug)
                continue

            if process_image_post(post, session, memes_metadata, new_ids, args.debug):
                total_memes += 1
            else:
                debug_log(f"  🟠 Found potential video post: {post.id}", args.debug)
                video_posts.append(post)

            if total_memes >= MEME_LIMIT:
                break

    # Process video posts
    for post in video_posts:
        if total_memes >= MEME_LIMIT:
            break
        if process_video_post(post, memes_metadata, new_ids, args.debug):
            total_memes += 1

    # Update cache and save metadata
    if new_ids:
        cached_ids.update(new_ids)
        with open(cache_file, "w") as f:
            json.dump(sorted(cached_ids), f)
        debug_log(f"🟢 Downloaded {len(new_ids)} new items.", args.debug)
    else:
        debug_log("🟠 No New Items Found!", args.debug)

    with open("cache/memes_metadata.json", "w") as f:
        json.dump(memes_metadata, f, indent=2)
    debug_log("🟢 Memes metadata collected.", args.debug)

if __name__ == "__main__":
    main()