import time
import nltk
import winsound
from faster_whisper import WhisperModel
from nltk.tokenize import sent_tokenize
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
from tqdm import tqdm
from pathlib import Path

#turns a timestamp into seconds
def timestamp_to_seconds(t):
    parts = t.strip().split(':')
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h = 0
        m, s = parts
    else:
        raise ValueError(f"Invalid timestamp: {t}")
    return int(h) * 3600 + int(m) * 60 + float(s)

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
        if start > cut_start and start < cut_end: 
            #if a segment begins in a cut range but ends outside of a cut range, return a new start time to begin the segment .1 seconds outside of the cut range
            return cut_end + 0.1
        if start < cut_start and end > cut_end: #check if the segment crosses a cut range but begins and ends outside of the cut range
            return "crosses"
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

#scrape the cut ranges located on the Panopto video editor website using Chrome driver and Selenium
#takes the link to video (must be link to editable video and user must sign in when driver pulls up the site)
def gather_cuts(u):
    options = Options()
    # Start ChromeDriver
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # Load the Panopto editor page
    url = u
    driver.get(url)
    wait = WebDriverWait(driver, 10)

    # Wait and click the edit tab
    try:
        parent_container = WebDriverWait(driver, 40).until(
            EC.element_to_be_clickable((By.ID, "editCutTabHeader"))
        )
        parent_container.click()
        wait.until(EC.visibility_of_element_located((By.ID, "editCutTabPane")))
    except:
        print("Could not find the editCutTabPane container.")
        driver.quit()
        exit()

    event_time_texts = []
    #wait until all cut events have been located on screen
    event_items = wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "index-event")))
    
    for i, event in enumerate(event_items):
        try:
            #Hover over the cut event
            ActionChains(driver).move_to_element(event).perform()

            # Click the toggle INSIDE the event (the three menu dots that appear on hover)
            toggle = event.find_element(By.CLASS_NAME, "event-edit-toggle")
            toggle.click()

            # Find the *visible* edit button that appears in a mini menu after toggle clicked
            edit_buttons = driver.find_elements(By.CLASS_NAME, "menu-edit-button")
            #only look for the edit button that is displayed on the screen currently
            edit_button = next(btn for btn in edit_buttons if btn.is_displayed())
            edit_button.click()
            
            # Get cut start/end time inputs
            start_div = wait.until(EC.visibility_of_element_located((By.CLASS_NAME, "edit-cut-start-time")))
            end_div = wait.until(EC.visibility_of_element_located((By.CLASS_NAME, "edit-cut-end-time")))
            start_time = start_div.get_attribute("value").strip()
            end_time = end_div.get_attribute("value").strip()
            event_time_texts.append((timestamp_to_seconds(start_time), timestamp_to_seconds(end_time)))
            print(f"Event {i+1}: Start = {start_time}, End = {end_time}")

            #close the modal
            try:
                close_button = driver.find_element(By.ID, "closeLink")
                close_button.click()
            except:
                from selenium.webdriver.common.keys import Keys
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.3)
        except Exception as e:
            print(f"Failed to process event {i+1}: {e}")
            continue
    # Done
    print("\nAll event times:")
    print(event_time_texts)

    driver.quit()
    return event_time_texts

#uses faster whisper version of OpenAi whisper model to transcribe video and produce segmented captions
#takes the downloaded video and the cut ranges collected from Panopto (if any)
def transcribe_whisper(input_file, cut_ranges):
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
                    #if word begins in a cut but ends outside of it, split the segment there and begin it outside of the cut range
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
                if cross_cut(current_start, word["start"], cut_ranges) == "crosses":
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
                text = ' '.join([w["word"] for w in current_words]).strip()
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
    srt_file = f"{input_file}.srt"
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
            print("(If you do not need to get the cuts from the video or elearning is down, press ENTER without entering a URL)")
            url = input("Enter the video page URL: ").strip()
            video_input = input("Enter the name of the video file (e.g., businessVid1.mp4) (no spaces, and make sure the file is in your downloads): ").strip()
            output_dir = Path.home() / "Downloads"
            input_file = output_dir / video_input
            if not input_file.exists():
                print(f"Error: File not found at {input_file}")
                exit(1)
            if url != "":
                cuts = gather_cuts(url)
            else:
                cuts = []
            start = time.time()
            transcribe_whisper(str(input_file), cuts)
            end = time.time()
            print(f"Time: {end-start:0.4f} seconds")
            pass 
        except Exception as e:
            print("An error occurred:")
            print(e)
        winsound.Beep(2500, 500)
        again = input("\nRun again? (y/n): ").strip().lower()
        if again != 'y':
            print("Goodbye!")
            break