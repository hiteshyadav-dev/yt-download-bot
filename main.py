# main.py
import os
import asyncio
import logging
import re
import subprocess
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import yt_dlp
from config import (
    BOT_TOKEN, DOWNLOAD_FOLDER, TELEGRAM_FILE_LIMIT_MB, 
    TARGET_COMPRESSED_SIZE_MB, MAX_DOWNLOAD_SIZE_MB, 
    DOWNLOAD_TIMEOUT, FFMPEG_PATH
)

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Store video info temporarily
user_video_info = {}


def clean_youtube_url(url):
    """Clean YouTube URL by removing tracking parameters"""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        
        # Remove tracking parameters
        tracking_params = ['si', 'feature', 'app', 'source']
        for param in tracking_params:
            params.pop(param, None)
        
        new_query = urlencode(params, doseq=True)
        cleaned = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
        
        logger.info(f"URL cleaned: {url} -> {cleaned}")
        return cleaned
    except Exception as e:
        logger.error(f"Error cleaning URL: {e}")
        return url


def get_video_duration(file_path):
    """Get video duration in seconds using FFmpeg"""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        duration = float(result.stdout.strip())
        return duration
    except Exception as e:
        logger.error(f"Error getting duration: {e}")
        return None


def compress_video(input_path, output_path, target_size_mb):
    """
    Compress video to target size using FFmpeg
    """
    try:
        logger.info(f"Starting compression: {input_path}")
        
        # Get video duration
        duration = get_video_duration(input_path)
        if not duration:
            logger.error("Could not get video duration")
            return False
        
        # Calculate target bitrate (in kbps)
        # Formula: (target_size_MB * 8192) / duration_seconds
        target_total_bitrate = int((target_size_mb * 8192) / duration)
        
        # Reserve some bitrate for audio (128 kbps)
        audio_bitrate = 128
        video_bitrate = max(target_total_bitrate - audio_bitrate, 100)  # Minimum 100 kbps
        
        logger.info(f"Duration: {duration}s, Target bitrate: {video_bitrate}k")
        
        # FFmpeg compression command
        cmd = [
            FFMPEG_PATH,
            '-i', input_path,
            '-c:v', 'libx264',  # H.264 codec
            '-b:v', f'{video_bitrate}k',  # Video bitrate
            '-maxrate', f'{video_bitrate}k',
            '-bufsize', f'{video_bitrate * 2}k',
            '-c:a', 'aac',  # Audio codec
            '-b:a', f'{audio_bitrate}k',  # Audio bitrate
            '-preset', 'fast',  # Encoding speed
            '-movflags', '+faststart',  # Web optimization
            '-y',  # Overwrite output
            output_path
        ]
        
        # Run FFmpeg
        logger.info(f"Running FFmpeg compression...")
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True,
            timeout=600  # 10 minutes max
        )
        
        if result.returncode == 0:
            # Check compressed file size
            compressed_size = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"Compression successful! Size: {compressed_size:.2f} MB")
            return True
        else:
            logger.error(f"FFmpeg error: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("Compression timeout!")
        return False
    except Exception as e:
        logger.error(f"Compression error: {e}")
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    welcome_message = (
        "🎬 *YouTube Video Downloader Bot*\n\n"
        "📌 *Features:*\n"
        "✅ Multiple quality options\n"
        "✅ Smart compression (बड़ी videos)\n"
        "✅ Automatic optimization\n"
        f"✅ Telegram limit: {TELEGRAM_FILE_LIMIT_MB}MB\n"
        "✅ URL auto-cleaning\n\n"
        "🚀 *How to use:*\n"
        "1️⃣ Send YouTube video link\n"
        "2️⃣ Select quality\n"
        "3️⃣ Get optimized video!\n\n"
        "💡 *Commands:*\n"
        "/start - Start bot\n"
        "/help - Help message"
    )
    await update.message.reply_text(welcome_message, parse_mode='Markdown')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command handler"""
    help_text = (
        "📖 *Help Guide*\n\n"
        "🔹 Send YouTube link\n"
        "🔹 Select quality option\n"
        "🔹 Bot will:\n"
        f"  • Send directly if <{TELEGRAM_FILE_LIMIT_MB}MB\n"
        f"  • Compress if >{TELEGRAM_FILE_LIMIT_MB}MB\n"
        "  • Provide link if compression fails\n\n"
        "⚠️ *Smart Features:*\n"
        "• Automatic compression\n"
        "• Quality optimization\n"
        "• Cleanup after sending\n\n"
        "❓ Issues? /start"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


def get_video_info(url):
    """Get video information using yt-dlp"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        logger.error(f"Error getting video info: {e}")
        return None


def format_size(size_bytes):
    """Convert bytes to human readable format"""
    if not size_bytes:
        return "Unknown"
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YouTube URL and show quality options"""
    url = update.message.text.strip()
    chat_id = update.effective_chat.id
    
    # Check YouTube URL
    if 'youtube.com' not in url and 'youtu.be' not in url:
        await update.message.reply_text(
            "❌ Please send a valid YouTube link!\n\n"
            "Example: https://www.youtube.com/watch?v=..."
        )
        return
    
    # Clean URL
    cleaned_url = clean_youtube_url(url)
    
    # Processing message
    processing_msg = await update.message.reply_text(
        "⏳ *Processing...*\n\n"
        "🔄 Cleaning URL...\n"
        "📡 Fetching info...",
        parse_mode='Markdown'
    )
    
    # Get video info
    info = get_video_info(cleaned_url)
    
    if not info:
        await processing_msg.edit_text(
            "❌ *Error!*\n\nCould not fetch video info.",
            parse_mode='Markdown'
        )
        return
    
    # Store info
    user_video_info[chat_id] = {
        'url': cleaned_url,
        'title': info.get('title', 'Unknown'),
        'formats': info.get('formats', [])
    }
    
    # Get video formats
    video_formats = {}
    
    for fmt in info.get('formats', []):
        if fmt.get('vcodec') != 'none' and fmt.get('acodec') != 'none':
            height = fmt.get('height')
            if height and height not in video_formats:
                filesize = fmt.get('filesize') or fmt.get('filesize_approx') or 0
                video_formats[height] = {
                    'format_id': fmt.get('format_id'),
                    'filesize': filesize,
                    'ext': fmt.get('ext', 'mp4')
                }
    
    # Sort qualities
    sorted_qualities = sorted(video_formats.keys(), reverse=True)
    
    # Create keyboard with ALL qualities
    keyboard = []
    
    for height in sorted_qualities:
        fmt_data = video_formats[height]
        filesize = fmt_data['filesize']
        
        if filesize:
            size_str = format_size(filesize)
            size_mb = filesize / (1024 * 1024)
            # Mark files that need compression
            if size_mb > TELEGRAM_FILE_LIMIT_MB:
                emoji = "🔧"  # Will be compressed
            else:
                emoji = "📹"  # Direct send
        else:
            size_str = "~Unknown"
            emoji = "📹"
        
        quality_text = f"{emoji} {height}p ({size_str})"
        keyboard.append([InlineKeyboardButton(quality_text, callback_data=f"quality_{height}")])
    
    # Add audio option
    keyboard.append([InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data="quality_audio")])
    
    if not keyboard or len(keyboard) <= 1:
        await processing_msg.edit_text(
            "❌ *No options found*",
            parse_mode='Markdown'
        )
        return
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Video info
    title = info.get('title', 'Unknown')
    duration = info.get('duration', 0)
    duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "Unknown"
    
    message = (
        f"✅ *Video Ready*\n\n"
        f"📝 {title[:40]}{'...' if len(title) > 40 else ''}\n"
        f"⏱ Duration: {duration_str}\n"
        f"📊 Qualities: {len(sorted_qualities)}\n\n"
        f"📥 *Select Quality:*\n\n"
        f"📹 = Direct send\n"
        f"🔧 = Will be compressed"
    )
    
    await processing_msg.edit_text(message, parse_mode='Markdown', reply_markup=reply_markup)


async def handle_quality_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quality selection with smart compression"""
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    
    if chat_id not in user_video_info:
        await query.edit_message_text("❌ Session expired!")
        return
    
    video_info = user_video_info[chat_id]
    url = video_info['url']
    title = video_info['title']
    quality_data = query.data.split('_')[1]
    
    await query.edit_message_text(
        f"⏳ *Downloading...*\n\n"
        f"🎥 Quality: {quality_data}p\n"
        f"Please wait...",
        parse_mode='Markdown'
    )
    
    try:
        # Download video
        file_path = await download_video_with_progress(url, quality_data, chat_id, query)
        
        if not file_path or not os.path.exists(file_path):
            await query.edit_message_text("❌ Download failed!")
            return
        
        # Get file size
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        logger.info(f"Downloaded: {file_size_mb:.2f} MB")
        
        # Check if compression needed
        if file_size_mb > TELEGRAM_FILE_LIMIT_MB:
            await query.edit_message_text(
                f"🔧 *Compressing...*\n\n"
                f"Original: {file_size_mb:.1f} MB\n"
                f"Target: {TARGET_COMPRESSED_SIZE_MB} MB\n\n"
                f"⏱ यह कुछ मिनट ले सकता है...",
                parse_mode='Markdown'
            )
            
            # Compress video
            compressed_path = file_path.rsplit('.', 1)[0] + '_compressed.mp4'
            compression_success = compress_video(file_path, compressed_path, TARGET_COMPRESSED_SIZE_MB)
            
            if compression_success and os.path.exists(compressed_path):
                # Use compressed file
                compressed_size_mb = os.path.getsize(compressed_path) / (1024 * 1024)
                logger.info(f"Compressed to: {compressed_size_mb:.2f} MB")
                
                # Delete original
                os.remove(file_path)
                file_path = compressed_path
                file_size_mb = compressed_size_mb
                
                # Check if still too large
                if file_size_mb > TELEGRAM_FILE_LIMIT_MB:
                    await query.edit_message_text(
                        f"⚠️ *Still Too Large*\n\n"
                        f"Compressed: {file_size_mb:.1f} MB\n"
                        f"Limit: {TELEGRAM_FILE_LIMIT_MB}MB\n\n"
                        f"📁 Location:\n`{file_path}`\n\n"
                        f"Try lower quality.",
                        parse_mode='Markdown'
                    )
                    # Cleanup
                    os.remove(file_path)
                    del user_video_info[chat_id]
                    return
            else:
                # Compression failed
                await query.edit_message_text(
                    f"❌ *Compression Failed*\n\n"
                    f"Size: {file_size_mb:.1f} MB\n"
                    f"Limit: {TELEGRAM_FILE_LIMIT_MB}MB\n\n"
                    f"📁 Location:\n`{file_path}`\n\n"
                    f"Try lower quality.",
                    parse_mode='Markdown'
                )
                # Cleanup
                os.remove(file_path)
                del user_video_info[chat_id]
                return
        
        # Upload to Telegram
        await query.edit_message_text(
            f"📤 *Uploading...*\n\n"
            f"📊 {file_size_mb:.1f} MB",
            parse_mode='Markdown'
        )
        
        if quality_data == 'audio':
            with open(file_path, 'rb') as audio_file:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_file,
                    title=title,
                    caption=f"🎵 {title}\n📊 {file_size_mb:.1f} MB",
                    parse_mode='Markdown'
                )
        else:
            with open(file_path, 'rb') as video_file:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video_file,
                    caption=f"🎬 {title}\n📊 {file_size_mb:.1f} MB | 🎥 {quality_data}p",
                    supports_streaming=True,
                    parse_mode='Markdown'
                )
        
        await query.edit_message_text(
            f"✅ *Success!*\n\n"
            f"📊 {file_size_mb:.1f} MB | 🎥 {quality_data}p",
            parse_mode='Markdown'
        )
        
        # Cleanup
        os.remove(file_path)
        del user_video_info[chat_id]
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)[:100]}")


async def download_video_with_progress(url, quality, chat_id, query):
    """Download video with progress updates"""
    output_template = os.path.join(DOWNLOAD_FOLDER, f"{chat_id}_%(title)s.%(ext)s")
    progress_data = {'last_update': 0}
    
    def progress_hook(d):
        if d['status'] == 'downloading':
            current_time = asyncio.get_event_loop().time()
            if current_time - progress_data['last_update'] > 10:
                progress_data['last_update'] = current_time
                
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                
                if total:
                    percent = (downloaded / total) * 100
                    progress_bar = "█" * int(percent / 5) + "░" * (20 - int(percent / 5))
                    
                    asyncio.create_task(query.edit_message_text(
                        f"⏳ *Downloading...*\n\n"
                        f"[{progress_bar}] {percent:.1f}%",
                        parse_mode='Markdown'
                    ))
    
    if quality == 'audio':
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': True,
        }
    else:
        ydl_opts = {
            'format': f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]',
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
            'progress_hooks': [progress_hook],
            'quiet': True,
            'no_warnings': True,
        }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if quality == 'audio':
                filename = filename.rsplit('.', 1)[0] + '.mp3'
            
            return filename
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Error: {context.error}")


def main():
    """Start bot"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(handle_quality_selection))
    application.add_error_handler(error_handler)
    
    logger.info("🚀 Bot starting with compression support...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped")
    finally:
        try:
            loop.close()
        except:
            pass
