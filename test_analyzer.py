import os
import re
import subprocess
import pickle
import json
import time
import yt_dlp
import whisper
from google import genai
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# Initialize the Gemini client securely with your key
client = genai.Client()

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']

# Load the local Whisper model once at startup (using the 'base' model for speed & accuracy)
print("[Init] Loading local Whisper speech-to-text model...")
whisper_model = whisper.load_model("base")

def manage_archive_storage():
    """Keeps the uploaded_shorts archive clean by removing files if it exceeds 15 items."""
    archive_folder = 'uploaded_shorts'
    if os.path.exists(archive_folder):
        files = sorted(
            [os.path.join(archive_folder, f) for f in os.listdir(archive_folder) if f.endswith('.mp4')],
            key=os.path.getmtime
        )
        while len(files) > 15:
            oldest_file = files.pop(0)
            try:
                os.remove(oldest_file)
                print(f"[Storage Cleanup] Removed old archive backup to save space: {os.path.basename(oldest_file)}")
            except Exception as e:
                print(f"[Storage Cleanup] Could not remove old file: {e}")

def check_and_download_new_content():
    print("\n[Teletraan 2] Scanning target channels in channels.txt for NEW uploads...")
    manage_archive_storage()
    
    if not os.path.exists('downloads'):
        os.makedirs('downloads')

    ydl_opts = {
        'extract_flat': False,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', 
        'max_filesize': 4 * 1024 * 1024 * 1024,  # Capped at 4GB maximum size
        'download_archive': 'downloaded_history.txt',
        'playlistend': 1,
        'outtmpl': 'downloads/%(title)s.%(ext)s',
    }

    if not os.path.exists('channels.txt'):
        print("Error: 'channels.txt' not found on your desktop!")
        return

    with open('channels.txt', 'r') as f:
        channels = [line.strip() for line in f if line.strip()]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for channel_url in channels:
            print(f"Checking channel: {channel_url}")
            try:
                ydl.download([channel_url])
            except Exception as e:
                print(f"Skipped for {channel_url}")

def time_to_seconds(time_str):
    time_str = time_str.strip()
    if ":" in time_str:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(float(parts[1]))
    return int(float(time_str))

def is_clip_already_processed(source_filename, start_sec, end_sec):
    """Prevents Teletraan 2 from duplicating identical cuts from the same video source."""
    history_file = 'processed_clips_history.txt'
    clip_signature = f"{source_filename}_{start_sec}_{end_sec}\n"
    
    processed = set()
    if os.path.exists(history_file):
        with open(history_file, 'r', encoding='utf-8') as f:
            processed = set(f.read().splitlines())
            
    if clip_signature.strip() in processed:
        return True
        
    with open(history_file, 'a', encoding='utf-8') as f:
        f.write(clip_signature)
    return False

def slice_and_crop_clip(input_file, start_sec, end_sec, output_filename):
    if not os.path.exists('shorts'):
        os.makedirs('shorts')
        
    output_path = os.path.join('shorts', output_filename)
    duration = end_sec - start_sec
    
    ffmpeg_cmd = [
        'ffmpeg', '-y',
        '-ss', str(start_sec),
        '-i', input_file,
        '-t', str(duration),
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
        '-c:v', 'libx264', '-preset', 'fast',
        '-c:a', 'aac',
        output_path
    ]
    
    print(f"[FFmpeg] Slicing & cropping vertical short: {output_filename}...")
    try:
        subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
        print(f"[Success] Saved to shorts/{output_filename}")
    except subprocess.CalledProcessError as e:
        print(f"[Error] FFmpeg failed: {e}")

def run_ai_strategy_and_slicing():
    download_folder = 'downloads'
    if not os.path.exists(download_folder):
        return
        
    for filename in os.listdir(download_folder):
        if filename.endswith(".mp4"):
            file_path = os.path.join(download_folder, filename)
            print(f"\n[Teletraan 2 AI Engine] Analyzing file for up to 7 viral SEO clips: {filename}")
            
            prompt = f"""
            I have just auto-downloaded a source video titled: "{filename}".
            As an expert YouTube Shorts SEO strategist, review the video length and identify up to 7 distinct, high-retention timestamp ranges for vertical clips (if the video is long enough to support up to 7 quality clips; if it's shorter, find as many distinct high-value clips as naturally possible up to 7).
            Avoid tiny duplicate segments; ensure each clip spans a meaningful standalone moment.
            For EACH clip, create a keyword-driven SEO title, an engaging description, and specific tags.
            
            You MUST format your response strictly using this block format for each clip (up to 7):
            
            CLIP 1:
            START: MM:SS, END: MM:SS
            TITLE: Your SEO Title Here #Shorts
            DESCRIPTION: Your keyword-rich description here. #Shorts #Gaming
            TAGS: tag1, tag2, tag3, tag4
            
            CLIP 2:
            START: MM:SS, END: MM:SS
            TITLE: Your SEO Title Here #Shorts
            DESCRIPTION: Your keyword-rich description here. #Shorts #Gaming
            TAGS: tag1, tag2, tag3, tag4
            
            (Continue this pattern up to CLIP 7 if content permits)
            """
            
            try:
                response = client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=prompt,
                )
                strategy_text = response.text
                print("\n--- GEMINI SEO STRATEGY & METADATA (UP TO 7 CLIPS) ---")
                print(strategy_text)
                print("----------------------------------------------------")
                
                clip_blocks = strategy_text.split("CLIP ")
                for block in clip_blocks[1:]:
                    try:
                        start_match = re.search(r'START:\s*([0-9:]+)', block, re.IGNORECASE)
                        end_match = re.search(r'END:\s*([0-9:]+)', block, re.IGNORECASE)
                        title_match = re.search(r'TITLE:\s*(.+)', block, re.IGNORECASE)
                        desc_match = re.search(r'DESCRIPTION:\s*(.+)', block, re.IGNORECASE)
                        tags_match = re.search(r'TAGS:\s*(.+)', block, re.IGNORECASE)
                        
                        if start_match and end_match:
                            start_sec = time_to_seconds(start_match.group(1))
                            end_sec = time_to_seconds(end_match.group(1))
                            
                            # Check duplication filter
                            if is_clip_already_processed(filename, start_sec, end_sec):
                                print(f"[Duplicate Filter] Skipped already processed clip range {start_sec}-{end_sec} from {filename}")
                                continue
                                
                            clip_title = title_match.group(1).strip() if title_match else filename[:50] + " #Shorts"
                            clip_desc = desc_match.group(1).strip() if desc_match else "AI-generated viral short. #Shorts"
                            raw_tags = tags_match.group(1).strip() if tags_match else "Shorts, Gaming, Viral"
                            clip_tags = [t.strip() for t in raw_tags.split(',')]
                            
                            safe_prefix = "".join(c for c in filename[:15] if c.isalnum() or c.isspace()).strip().replace(" ", "_")
                            unique_id = f"{safe_prefix}_{int(time.time())}_{start_sec}"
                            output_name = f"short_{unique_id}.mp4"
                            
                            # Slice video (Cropping happens first here)
                            slice_and_crop_clip(file_path, start_sec, end_sec, output_name)
                            
                            # Save SEO metadata as a sidecar JSON file matching the video name
                            meta_data = {
                                "title": clip_title[:95],
                                "description": clip_desc,
                                "tags": clip_tags
                            }
                            meta_path = os.path.join('shorts', f"short_{unique_id}.json")
                            with open(meta_path, 'w', encoding='utf-8') as mf:
                                json.dump(meta_data, mf)
                                
                    except Exception as clip_parse_err:
                        print(f"Error parsing individual clip block: {clip_parse_err}")

            except Exception as e:
                print(f"AI Analysis error: {e}")
            
            print(f"[Cleanup] Deleting raw source file: {filename}")
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Could not delete file: {e}")

def burn_subtitles_onto_short(file_path):
    """Phase 5: Transcribes audio using Whisper and burns Komika Axis captions below center post-crop."""
    print(f"[Teletraan 2 - Whisper] Transcribing audio for Komika Axis captions: {os.path.basename(file_path)}...")
    try:
        result = whisper_model.transcribe(file_path, fp16=False, language="en")
        segments = result.get('segments', [])
        
        if not segments:
            print("[Whisper] No speech detected to caption. Skipping subtitle burn.")
            return file_path

        srt_path = file_path.replace('.mp4', '.srt')
        with open(srt_path, 'w', encoding='utf-8') as srt_file:
            for idx, segment in enumerate(segments, start=1):
                start_time = format_srt_time(segment['start'])
                end_time = format_srt_time(segment['end'])
                text = segment['text'].strip()
                srt_file.write(f"{idx}\n{start_time} --> {end_time}\n{text}\n\n")

        captioned_path = file_path.replace('.mp4', '_captioned.mp4')
        safe_srt_path = srt_path.replace('\\', '/')
        
        # Styled with Komika Axis font, positioned just below the vertical center point
        style_string = (
            "FontName=Komika Axis,FontSize=20,"
            "PrimaryColour=&H00FFFF&,OutlineColour=&H000000&,"
            "BackColour=&H80000000&,BorderStyle=3,Outline=2,Shadow=1,"
            "Alignment=5,MarginV=120,MarginL=60,MarginR=60"
        )
        
        vf_filter = f"subtitles='{safe_srt_path}':force_style='{style_string}'"
        
        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-i', file_path,
            '-vf', vf_filter,
            '-c:v', 'libx264', '-preset', 'fast',
            '-c:a', 'copy',
            captioned_path
        ]
        
        print(f"[FFmpeg] Burning Komika Axis captions post-crop...")
        subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
        
        if os.path.exists(srt_path):
            os.remove(srt_path)
        if os.path.exists(file_path):
            os.remove(file_path)
            
        print(f"[Success] Komika Axis captions burned successfully!")
        return captioned_path

    except Exception as e:
        print(f"[Warning] Caption generation failed ({e}), uploading without subtitles.")
        return file_path

def format_srt_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millisecs = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millisecs:03d}"

def upload_shorts_to_youtube():
    shorts_folder = 'shorts'
    archive_folder = 'uploaded_shorts'
    
    if not os.path.exists(shorts_folder) or not os.listdir(shorts_folder):
        print("\n[Teletraan 2 Uploader] No shorts found in 'shorts/' folder to process.")
        return

    if not os.path.exists(archive_folder):
        os.makedirs(archive_folder)

    if not os.path.exists('client_secret.json'):
        print("\n[Upload Error] 'client_secret.json' not found on desktop! Skipping auto-upload.")
        return

    print("\n--- TELETRAAN 2: STARTING CAPTIONING & SEO UPLOADER ---")
    
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    youtube = build('youtube', 'v3', credentials=creds)

    for filename in os.listdir(shorts_folder):
        if filename.endswith(".mp4") and not filename.endswith("_captioned.mp4"):
            file_path = os.path.join(shorts_folder, filename)
            base_name = filename.replace('.mp4', '')
            
            meta_path = os.path.join(shorts_folder, f"{base_name}.json")
            seo_title = "Viral Short #Shorts"
            seo_desc = "Auto-generated viral short. #Shorts"
            seo_tags = ["Shorts", "Viral", "Gaming"]
            
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, 'r', encoding='utf-8') as mf:
                        meta_data = json.load(mf)
                        seo_title = meta_data.get("title", seo_title)
                        seo_desc = meta_data.get("description", seo_desc)
                        seo_tags = meta_data.get("tags", seo_tags)
                except Exception as meta_err:
                    print(f"Could not load metadata JSON: {meta_err}")

            # Burn captions post-crop with Komika Axis font
            final_file_path = burn_subtitles_onto_short(file_path)
            final_filename = os.path.basename(final_file_path)
            
            print(f"[YouTube Uploader] Uploading SEO Optimized Short: {seo_title}...")
            
            body = {
                'snippet': {
                    'title': seo_title,
                    'description': seo_desc,
                    'tags': seo_tags,
                    'categoryId': '20'
                },
                'status': {
                    'privacyStatus': 'public',
                    'selfDeclaredMadeForKids': False
                }
            }

            try:
                media = MediaFileUpload(final_file_path, chunksize=-1, resumable=True)
                request = youtube.videos().insert(
                    part='snippet,status',
                    body=body,
                    media_body=media
                )
                response = request.execute()
                print(f"[Success] Uploaded SEO short! Video ID: {response.get('id')}")
                
                del media
                
                target_archive_path = os.path.join(archive_folder, final_filename)
                if os.path.exists(target_archive_path):
                    os.remove(target_archive_path)
                os.rename(final_file_path, target_archive_path)
                
                if os.path.exists(meta_path):
                    os.remove(meta_path)
                    
                print(f"[Archive] Moved {final_filename} and cleaned up metadata.")
                
            except Exception as e:
                print(f"[Error] Failed to upload {final_filename}: {e}")

if __name__ == "__main__":
    print("--- TELETRAAN 2 FULLY ONLINE (10-MIN INTERVAL, KOMIKA AXIS FONT, ANTI-DUPE FILTER) ---")
    while True:
        try:
            check_and_download_new_content()
            run_ai_strategy_and_slicing()
            upload_shorts_to_youtube()
        except Exception as loop_err:
            print(f"[Loop Error]: {loop_err}")
            
        print("\n[Teletraan 2] Cycle complete. Sleeping for 10 minutes until next check...")
        time.sleep(600)  # 600 seconds = 10 minutes