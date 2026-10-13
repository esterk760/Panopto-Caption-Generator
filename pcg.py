import time
import nltk
import winsound
from faster_whisper import WhisperModel
from nltk.tokenize import sent_tokenize
from tqdm import tqdm
from pathlib import Path
import requests
import os
import sys
from requests_oauthlib import OAuth2Session
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import subprocess
from dotenv import load_dotenv
# Disable SSL requirement only for local testing redirect URLs
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
if getattr(sys, 'frozen', False):
    # Running as a compiled .exe; look in the actual folder where the .exe sits
    base_path = os.path.dirname(sys.executable)
else:
    # Running as a normal .py script; look in the script's directory
    base_path = os.path.dirname(os.path.abspath(__file__))

FFMPEG_PATH = os.path.join(base_path, "ffmpeg", "ffmpeg.exe")

if not os.path.exists(FFMPEG_PATH):
    print("ERROR: FFmpeg was not found.")
    print(f"Expected location: {FFMPEG_PATH}")
    input("Press Enter to exit...")
    sys.exit(1)

# Point to the .env file in that directory
dotenv_path = os.path.join(base_path, '.env')
load_dotenv(dotenv_path)
PANOPTO_SERVER = os.getenv("PANOPTO_SERVER")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

AUTHORIZATION_BASE_URL = f"https://{PANOPTO_SERVER}/Panopto/oauth2/connect/authorize"
TOKEN_URL = f"https://{PANOPTO_SERVER}/Panopto/oauth2/connect/token"
REDIRECT_URI = "http://localhost:5000"  # This must be added to your Panopto client registration!
API_BASE_URL = f"https://{PANOPTO_SERVER}/Panopto/api"


# Global variable to safely capture the full redirect URL across threads/classes
captured_redirect_url = None
class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Temporary local server handler to catch the Panopto OAuth2 redirect."""
    
    def do_GET(self):
        global captured_redirect_url
        
        # Capture the full URL requested by the browser redirect
        captured_redirect_url = f"http://localhost:5000{self.path}"
        
        # Send a friendly HTML success page back to the user's browser
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        
        html_response = """
        <html>
            <body style="font-family: Arial, sans-serif; text-align: center; padding-top: 50px;">
                <h2 style="color: #2e7d32;">✅ Authentication Successful!</h2>
                <p>You can close this browser tab now and return to your application terminal.</p>
            </body>
        </html>
        """
        self.wfile.write(html_response.encode("utf-8"))

    def log_message(self, format, *args):
        # Suppress standard local server console logs to keep your terminal clean
        return
    
#for getting authentication from Panopto, spins up a temporary local server to catch the redirect from the browser after SSO login
def get_creator_session():
    global captured_redirect_url
    captured_redirect_url = None  # Reset state

    # 1. Initialize OAuth2 session
    panopto = OAuth2Session(CLIENT_ID, redirect_uri=REDIRECT_URI, scope=["api", "openid"])
    
    # 2. Get the authorization page URL
    authorization_url, state = panopto.authorization_url(AUTHORIZATION_BASE_URL)
    
    # 3. Spin up the temporary local server on port 5000
    # Note: Ensure REDIRECT_URI in your Panopto developer portal is exactly http://localhost:5000
    server_address = ('localhost', 5000)
    httpd = HTTPServer(server_address, OAuthCallbackHandler)
    
    print("🚀 Launching your web browser for Panopto SSO login...")
    # Automatically open the login page in the user's default browser
    webbrowser.open(authorization_url)
    
    print("⏳ Waiting for browser authentication response...")
    # This blocks execution and listens for exactly ONE incoming request from the browser redirect
    httpd.handle_request()
    
    # Securely close the temporary server right after capturing the data
    httpd.server_close()

    # 4. Fetch the tokens using the captured background URL
    if not captured_redirect_url:
        print("❌ Handshake failed: No redirect data captured from browser.")
        return None

    try:
        tokens = panopto.fetch_token(
            TOKEN_URL,
            client_secret=CLIENT_SECRET,
            authorization_response=captured_redirect_url
        )
        print("🎉 Successfully authenticated with your Creator permissions!")
        return panopto
    except Exception as e:
        print(f"❌ Handshake failed: {e}")
        return None

# Retrieves the primary video stream URL for a given Panopto session ID
def get_video_download(session, session_id):
    session_endpoint = f"{API_BASE_URL}/sessions/{session_id}/streams"
    response = session.get(session_endpoint)
    if response.status_code == 200:
        data = response.json()
        primary_session = next((item for item in data if item.get("IsPrimary") == True), None)
        download_url = primary_session.get("Url", None)
        print("✅ Successfully fetched session data.")
        return download_url
    else:
        print(f"❌ Failed to fetch session. Status Code: {response.status_code}")
        print(response.text)
        return None

"""
    Downloads a Panopto video stream. Resolves 403 Forbidden errors by 
    exchanging the OAuth2 Bearer token for file-system session cookies.
"""
def download_panopto_video(video_url, panopto_session, output_audio="audio.mp4"):
    
    if not video_url or video_url == "#":
        print("❌ Error: Download URL is invalid or downloading is disabled.")
        return None
    # get_video_download returns a direct .mp4 link if available, otherwise it may return a manifest URL for adaptive streaming.
    if video_url.endswith(".mp4"):
        print("🔑 Authenticating file delivery system (exchanging Token for Cookies)...")
        try:
            # Force-inject browser cookies into the session using your active OAuth2 token
            cookie_auth_url = f"https://{PANOPTO_SERVER}/Panopto/api/v1/auth/legacyLogin"
            cookie_response = panopto_session.get(cookie_auth_url)
            cookie_response.raise_for_status()
        except Exception as e:
            print(f"⚠️ Warning: Could not explicitly sync cookies: {e}")
            print("Attempting to proceed with download anyway...")

        print(f"📡 Initiating secure video stream from: {video_url}")
        try:
            # Stream the request with cookies now present in the panopto_session context
            with panopto_session.get(video_url, stream=True) as response:
                if response.status_code == 403:
                    print("❌ Error 403: Still Forbidden. Your account lacks explicit download permissions for this folder.")
                    return None
                    
                response.raise_for_status()
                
                with open(output_audio, 'wb') as file:
                    print(f"📥 Downloading file to '{output_audio}'...")
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            file.write(chunk)
                            
            print(f"✅ Successfully downloaded video to: {output_audio}")
            return output_audio
            
        except Exception as e:
            print(f"❌ Failed to download video: {e}")
            return None
    else: # The URL is likely a manifest for adaptive streaming
    
        print("[*] Contacting CloudFront manifest to strip audio...")

        # Setup the FFmpeg execution command
        ffmpeg_cmd = [
            FFMPEG_PATH,
            "-y",                  # Auto-overwrite if file exists
            "-i", video_url,    # Input master manifest URL (current video_url)
            "-vn",                 # Disable video recording (only grab audio)
            "-acodec", "libmp3lame", # Convert the stream directly to high-compatibility MP3
            "-q:a", "4",           # Target high-quality VBR encoding
            output_audio
        ]

        try:
            # Run the stream collection
            process = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if process.returncode == 0:
                print(f"[√] Success! File saved as: {output_audio}")
                return output_audio
            else:
                print(f"[X] FFmpeg processing error:\n{process.stderr}")
        except FileNotFoundError:
            print("[X] Error: Ensure 'ffmpeg' is installed and loaded in your system environment PATH variables.")


# Retrieves list of session cuts from Panopto REST endpoint given a panopto creator session.
def get_cuts(session, session_id):
    cuts_endpoint = f"{API_BASE_URL}/sessions/{session_id}/cuts"
    cuts = []
    # Optional parameters for filtering results
    params = {
        "pageNumber": 0
    }
    response = session.get(cuts_endpoint, params=params)
    
    if response.status_code == 200:
        data = response.json()
        print("\n--- Cuts List ---")
        cuts_list = data if isinstance(data, list) else []
        for cut in cuts_list:
            start_time = cut.get("Start")/10000000  # Convert from ticks to seconds
            end_time = cut.get("Duration")/10000000 + start_time 
            cuts.append((start_time, end_time))
        return cuts
    else:
        print(f"❌ Failed to fetch cuts. Status Code: {response.status_code}")
        print(response.text)
        return None
    

#checks whether a segment crosses a cut at all
def crosses_cut(start, end, cut_ranges):
    for cut_start, cut_end in cut_ranges:
        if end < cut_start:
            return False
        if start < cut_end and end > cut_start:
            return True
    return False

#checks for specific conditions if a segment crosses a cut
def cross_cut(start, end, cut_ranges):
    for cut_start, cut_end in cut_ranges:
        if end < cut_start: #stop loop once cut ranges being checked are already past the segment given
            return "none"
        if start > cut_start and end < cut_end: #check if a segment is completely within a cut range
            return "in"
        if (start > cut_start and start < cut_end) or (start < cut_start and end > cut_end): 
            #if a segment begins in a cut range but ends outside of a cut range, return a new start time to begin the segment .1 seconds outside of the cut range
            return cut_end + 0.1
        # if start < cut_start and end > cut_end: #check if the segment crosses a cut range but begins and ends outside of the cut range
        #     return "crosses"
    return "none"

def format_srt_time(seconds):
    return f"{int(seconds // 3600):02}:{int((seconds % 3600) // 60):02}:{int(seconds % 60):02},{int((seconds * 1000) % 1000):03}"

#given a dictionary of words with their timestamps, find the index of greatest gap between words and split the words into segments of <=12 words
def split_segments(words, short_seg):
    gaps = []
    for i in range(3, len(words)-3):
        gap = words[i]['end'] - words[i-1]['end']
        gaps.append((gap, i))  # store the gap and index to split at
    # Find the largest gap
    max_gap, split_index = max(gaps, key=lambda x: x[0])
    # First sub-segment
    first_words = words[:split_index]
    #if the segment is short enough, add it to short_seg, else recurse
    if len(first_words) <= 12:
        short_seg.append({
            "start": first_words[0]["start"],
            "end": first_words[-1]["end"],
            "text": ''.join([w["word"] for w in first_words]).strip()
            })
    else:
        split_segments(first_words, short_seg)
    # Second sub-segment (with same logic)
    second_words = words[split_index:]
    if len(second_words) <= 12:
        short_seg.append({
            "start": second_words[0]["start"],
            "end": second_words[-1]["end"],
            "text": ''.join([w["word"] for w in second_words]).strip()
            })
    else:
        split_segments(second_words, short_seg)



#uses faster whisper version of OpenAi whisper model to transcribe video and produce segmented captions
#takes the downloaded video and the cut ranges collected from Panopto (if any)
def transcribe_whisper(input_file, cut_ranges, output_file):
    print("🎙️ Starting transcription with faster-whisper...")
    model = WhisperModel("small", compute_type="int8", device="cpu")
    w_segments, _ = model.transcribe(
        input_file,
        language="en",
        word_timestamps=True
    )
    #extract all the words produced by whisper to customize the segmentation
    all_words = []
    #taking the words from the segments provided by whisper
    for segment in w_segments:
        all_words.extend(segment.words)

    full_text = ''.join([w.word for w in all_words])
    #uses nltk to split the whisper text into sentences
    sentences = sent_tokenize(full_text)
    segments = []
    i = 0
    #turn the sentences into whisper-formatted transcription segments
    for sentence in sentences:
        words_in_sentence = []
        text = ''
        while i < len(all_words) and len(text) < len(sentence):
            word = all_words[i]
            text += word.word
            words_in_sentence.append({
                "start": word.start,
                "end": word.end,
                "word": word.word,
            })
            i += 1
        if words_in_sentence:
            segments.append({
                "text": ''.join([w['word'] for w in words_in_sentence]),
                "start": words_in_sentence[0]['start'],
                "end": words_in_sentence[-1]['end'],
                "words": words_in_sentence
            })

    short_segments = []

    # Split each original segment to avoid cut ranges and to keep them short
    for segment in tqdm(segments, desc="Processing Whisper segments"):
        words = segment.get("words", [])
        if not words:
            continue
        #checking if the segment crosses a cut range at all and if so, split based on specific conditions
        if crosses_cut(words[0]["start"], words[-1]["end"], cut_ranges):
            current_words = []
            current_start = words[0]["start"]
            #go through each word in the segment to find which one crosses a cut
            for word in words:
                check_cuts = cross_cut(word["start"], word["end"], cut_ranges)
                if check_cuts == "in": #leave the word out of the segment if it is completely within a cut range
                    continue
                if type(check_cuts) is float: 
                    #if word begins in or before a cut but ends outside of it, split the segment there and begin it outside of the cut range
                    if current_words:
                        text = ''.join([w["word"] for w in current_words]).strip()
                        short_segments.append({
                            "start": current_start,
                            "end": current_words[-1]["end"],
                            "text": text
                        })
                    current_words = [word]
                    current_start = check_cuts
                    continue
                
                #if the current start of the segment up to the word currently being checked crosses a cut 
                # but both begins and ends outside of the cut range, start a new segment/split the segment
                if type(cross_cut(current_start, word["start"], cut_ranges)) is float:
                    if current_words:
                        text = ''.join([w["word"] for w in current_words]).strip()
                        short_segments.append({
                            "start": current_start,
                            "end": current_words[-1]["end"],
                            "text": text
                        })
                    current_words = [word]
                    current_start = word["start"]
                    continue
                #if the word does not cross a cut, just add to segment until length is >= 10 or if gap between words is >2 seconds
                last_end = word["start"]
                if not current_words:
                    current_start = word["start"]
                current_words.append(word)
                if len(current_words) > 1:
                    last_end = current_words[-2]["end"]
                duration = word["end"] - current_start
                d = word["start"] - last_end
                if d >= 2 or len(current_words) >= 10:
                    text = ''.join([w["word"] for w in current_words]).strip()
                    short_segments.append({
                        "start": current_start,
                        "end": word["end"],
                        "text": text
                    })
                    current_words = []

            # Handle any remaining words in the last segment
            if current_words:
                text = ''.join([w["word"] for w in current_words]).strip()
                short_segments.append({
                    "start": current_start,
                    "end": current_words[-1]["end"],
                    "text": text
                })
            continue
        #if segment does not cross cut but is too long, split
        if len(words) > 12:
            split_segments(words, short_segments)
            continue
        #else, just add it to the final set of segments
        short_segments.append(segment)
        short_segments[-1]["text"] = short_segments[-1]["text"].strip()

    # Write to SRT file
    srt_file = output_file
    with open(srt_file, "w", encoding="utf-8") as f:
        for idx, caption in enumerate(short_segments):
            start_time_srt = format_srt_time(caption["start"])
            end_time_srt = format_srt_time(caption["end"])
            text = caption["text"]

            f.write(f"{idx + 1}\n")
            f.write(f"{start_time_srt} --> {end_time_srt}\n")
            f.write(f"{text}\n\n")
    print(f"SRT saved to {srt_file}")

#finds video located in user downloads, if correct panopto link given gathers cut ranges from editor,
#transcribes the video and writes captions to srt file located in user downloads, beeps at finish
if __name__ == "__main__":
    while True:
        try:
            result = subprocess.run(
                [FFMPEG_PATH, "-version"],
                capture_output=True,
                text=True
            )

            print(result.stdout[:500])
            print("FFmpeg return code:", result.returncode)
            panopto_session = get_creator_session()
            video_session = input("Enter the Panopto session ID from the video URL: ").strip()
            captions_name = input("Enter the desired name for the captions file (e.g., humanities2a): ").strip()
            output_dir = Path.home() / "Downloads" / f"{captions_name}.srt" #directory where srt is saved, currently set to user downloads
            video = get_video_download(panopto_session, video_session) #returns url of video to download, either direct .mp4 or manifest
            cuts = get_cuts(panopto_session, video_session) 
            downloaded_video = download_panopto_video(video, panopto_session) #downloads video to temporary audio file in current working directory
            if downloaded_video and os.path.exists(downloaded_video):
                start = time.time()
                transcribe_whisper(str(downloaded_video), cuts, str(output_dir))
                end = time.time()
                print(f"Time: {end-start:0.4f} seconds")
                try:
                    os.remove(downloaded_video) #remove temporary audio file after transcription is complete
                    print(f"🗑️ Successfully deleted temporary file: {downloaded_video}")
                except Exception as cleanup_error:
                    print(f"⚠️ Could not delete the file: {cleanup_error}")
            pass 
        except Exception as e:
            print("An error occurred:")
            print(e)
        winsound.Beep(2500, 500)
        again = input("\nRun again? (y/n): ").strip().lower()
        if again != 'y':
            print("Goodbye!")
            break
