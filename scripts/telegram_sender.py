import os
import re
import json
import asyncio
import argparse
from telegram import Bot

def parse_args():
    parser = argparse.ArgumentParser(description='Telegram Meme Sender')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--token', required=True, help='Telegram Bot Token')
    parser.add_argument('--chat-id', required=True, help='Telegram Chat ID')
    parser.add_argument('--metadata', default='cache/memes_metadata.json', 
                       help='Path to metadata file')
    return parser.parse_args()

def debug_log(msg, show_debug=True):
    if show_debug:
        print(msg)

def escape_markdown(text):
    """Escapes Markdown-sensitive characters."""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f'([{"".join(re.escape(c) for c in escape_chars)}])', r'\\\1', text)

def build_caption(meme):
    if meme['type'] == 'image':
        return f"""🎉 **New Meme Alert!**
📜 *Title:* {escape_markdown(meme.get('title', 'No Title'))}
🖋️ *Author:* {escape_markdown(meme.get('author', 'Unknown'))}
👍 *Upvotes:* {meme.get('upvotes', '0')}
🏷️ *Subreddit:* r/{escape_markdown(meme.get('subreddit', '?'))}"""
    else:
        return f"""🎉 **New Video Meme Alert!**
📜 *Title:* {escape_markdown(meme.get('title', 'No Title'))}
🖋️ *Author:* {escape_markdown(meme.get('author', 'Unknown'))}
👍 *Upvotes:* {meme.get('upvotes', '0')}
🏷️ *Subreddit:* r/{escape_markdown(meme.get('subreddit', '?'))}"""

async def send_meme_async(bot, meme, chat_id, show_debug=True):
    file_path = os.path.join('cache', meme['filename'])
    caption = build_caption(meme)
    try:
        if meme['type'] == 'image':
            with open(file_path, 'rb') as img:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=img,
                    caption=caption,
                    parse_mode='Markdown'
                )
                debug_log(f"🟢 Sent image Meme ID: {meme['id']}", show_debug)
        elif meme['type'] == 'video':
            with open(file_path, 'rb') as vid:
                await bot.send_video(
                    chat_id=chat_id,
                    video=vid,
                    caption=caption,
                    parse_mode='Markdown',
                    supports_streaming=True
                )
                debug_log(f"🟢 Sent video Meme ID: {meme['id']}", show_debug)
    except Exception as e:
        debug_log(f"🔴 Failed to send Meme ID {meme['id']}: {e}", show_debug)

async def main():
    args = parse_args()
    
    if not os.path.exists(args.metadata):
        debug_log("🟠 No metadata file found. Nothing to send.", args.debug)
        return

    try:
        with open(args.metadata, 'r') as f:
            memes = json.load(f)
        debug_log(f"🟢 Loaded {len(memes)} memes from metadata.", args.debug)

        bot = Bot(token=args.token)
        for meme in memes:
            await send_meme_async(bot, meme, args.chat_id, args.debug)

        debug_log("🟢 All memes have been processed.", args.debug)

    except Exception as e:
        debug_log(f"🔴 Error processing memes: {e}", args.debug)

if __name__ == '__main__':
    asyncio.run(main())