# config.py
import os

# Bot Token from environment variable (Railway पर set करेंगे)
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_TOKEN_HERE')

# Download folder
DOWNLOAD_FOLDER = os.path.join(os.getcwd(), "downloads")

# Telegram limits
TELEGRAM_FILE_LIMIT_MB = 50
TARGET_COMPRESSED_SIZE_MB = 45
MAX_DOWNLOAD_SIZE_MB = None
DOWNLOAD_TIMEOUT = 600
FFMPEG_PATH = "ffmpeg"

# Create downloads folder
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)
