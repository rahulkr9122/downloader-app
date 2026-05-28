# Video Downloader Website

A simple Flask website that lets you paste a YouTube or Instagram video URL and download the video file.

## Setup

1. Open a terminal in `c:\Users\rahul\Downloads\testdownloadapp`
2. Install dependencies:
   ```powershell
   python -m pip install -r requirements.txt
   ```
3. Run the site:
   ```powershell
   python app.py
   ```
4. Open `http://127.0.0.1:5000` in your browser.

## Notes

- This app uses `yt-dlp` to download videos.
- It only supports YouTube and Instagram video URLs.
- Downloading videos may violate the terms of service of the source website. Use responsibly.

## Publish the site

### Free option: Render

1. Push the project to a GitHub repository.
2. Create a free Render account at https://render.com.
3. Connect your GitHub repo and create a new Web Service.
4. Use these settings:
   - Environment: `Python`
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
   - Plan: `Free`
5. Render will assign a URL you can share publicly.

Render can deploy this project immediately with `render.yaml`.

### Local Docker option

1. Build the image:
   ```powershell
   docker build -t rahuls-downloader .
   ```
2. Run it:
   ```powershell
   docker run -p 5000:5000 rahuls-downloader
   ```
3. Open `http://127.0.0.1:5000`.

### Important

- Your server must have `yt-dlp` installed (already in `requirements.txt`).
- If you share the app publicly, be careful about terms of service for YouTube and Instagram.
