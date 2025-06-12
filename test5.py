#uses python version 3.11.9
#uses faster whisper model and spacy (en_core_web_sm) for speech-to-text and clause splitting
import time
import en_core_web_sm
from faster_whisper import WhisperModel
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
from difflib import SequenceMatcher

#turns panopto formatted timestamps into seconds
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

#checks for certain conditions if a segment crosses a cut in Panopto
def cross_cut(start, end, cut_ranges):
    for cut_start, cut_end in cut_ranges:
        if end < cut_start: #stop looping if segment is definitely not crossed by a cut
            return "none"
        if start > cut_start and end < cut_end: #a segment is within a cut range
            return "in"
        if start > cut_start and start < cut_end: #a segment starts in a cut range
            return cut_end + 0.1 #return the end of the cut+0.1 so the segment starts after the cut
        if start < cut_start and end > cut_end: #if the cut is within the segment
            return "crosses"
    return "none"
#checks if a segment crosses a cut period in Panopto
def crosses_cut(start, end, cut_ranges):
    for cut_start, cut_end in cut_ranges:
        if end < cut_start:
            return False
        if start < cut_end and end > cut_start:
            return True
    return False

#formats seconds into srt format
def format_srt_time(seconds):
    return f"{int(seconds // 3600):02}:{int((seconds % 3600) // 60):02}:{int(seconds % 60):02},{int((seconds * 1000) % 1000):03}"

#method for splitting segments larger than 12 words based on where the biggest pauses are
def split_segments(words, short_seg):
    gaps = []
    #checks the middle 20% of the segment
    for i in range(round(len(words)*.4), round(len(words)*.6)):
        gap = words[i]['start'] - words[i-1]['end']
        gaps.append((gap, i))  # store the gap and index to split at

    # Finds the largest gap
    max_gap, split_index = max(gaps, key=lambda x: x[0])
    # First sub-segment
    first_words = words[:split_index]
    if len(first_words) <= 12: #add the segment to the final list
        short_seg.append({
            "start": first_words[0]["start"],
            "end": first_words[-1]["end"],
            "text": ''.join([w["word"] for w in first_words]).strip()
        })
    else: #recurse
        split_segments(first_words, short_seg)

    # Second sub-segment (same logic as first)
    second_words = words[split_index:]
    if len(second_words) <= 12:
        short_seg.append({
            "start": second_words[0]["start"],
            "end": second_words[-1]["end"],
            "text": ''.join([w["word"] for w in second_words]).strip()
        })
    else:
        split_segments(second_words, short_seg)

#using selenium to grab the Panopto video cuts from the editor via webscraping
#(not reliable, most likely to cause errors if Panopto editor changes anything)
#(only works on Chrome browsers)
def gather_cuts(u):
    # Set up Chrome options
    options = Options()
    # options.add_argument('--headless')  # Optional: run in headless mode

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
    
    #cut time texts
    event_time_texts = []
    event_items = wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "index-event")))

    for i, event in enumerate(event_items):
        try:
            # Hover over the event
            ActionChains(driver).move_to_element(event).perform()

            # Find the menu toggle INSIDE the event
            toggle = event.find_element(By.CLASS_NAME, "event-edit-toggle")
            toggle.click()

            # Find the *visible* edit button
            edit_buttons = driver.find_elements(By.CLASS_NAME, "menu-edit-button")
            edit_button = next(btn for btn in edit_buttons if btn.is_displayed())
            edit_button.click()
            
            # Get cut start/end time inputs
            start_div = wait.until(EC.visibility_of_element_located((By.CLASS_NAME, "edit-cut-start-time")))
            end_div = wait.until(EC.visibility_of_element_located((By.CLASS_NAME, "edit-cut-end-time")))
            start_time = start_div.get_attribute("value").strip()
            end_time = end_div.get_attribute("value").strip()
            event_time_texts.append((timestamp_to_seconds(start_time), timestamp_to_seconds(end_time)))
            print(f"Event {i+1}: Start = {start_time}, End = {end_time}")

            # Close the modal window
            try:
                close_button = driver.find_element(By.ID, "closeLink")
                close_button.click()
                # wait.until(EC.invisibility_of_element_located((By.ID, "modalWindow")))
            except:
                # Or press ESC to close it
                from selenium.webdriver.common.keys import Keys
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.3)

            # # Wait for the menu to disappear
            # wait.until(EC.invisibility_of_element_located((By.CLASS_NAME, "edit-event-menu")))

        except Exception as e:
            print(f"Failed to process event {i+1}: {e}")
            continue


    # Done
    print("\nAll event times:")
    print(event_time_texts)

    driver.quit()
    return event_time_texts

#main trascription actions
def transcribe_whisper(input_file, cut_ranges):
    model = WhisperModel("tiny", compute_type="int8")
    w_segments, _ = model.transcribe(
        input_file,
        language="en",
        word_timestamps=True
    )
    #take the segments generated by whisper and extract all words
    all_words = []
    for segment in w_segments:
        all_words.extend(segment.words)

    #join the words as a string
    full_text = ''.join([w.word for w in all_words])
    # Load spaCy model
    nlp = en_core_web_sm.load()
    doc = nlp(full_text)
    # Extract clause-like segments
    clauses = []
    clause = []
    for token in doc:
        if token.dep_ == 'cc' and token.head.dep_ != 'compound':
        # If it's a coordinating conjunction (e.g., "and", "but") split and add to clauses
            if clause:
                clauses.append(' '.join(clause).strip())
            clause = [token.text]  # start next clause with the conjunction
        elif token.is_punct and token.text not in ('"', "'"):
            # Keep punctuation in current clause
            clause.append(token.text)
            clauses.append(' '.join(clause).strip())
            clause = []
        else:
            clause.append(token.text)
    #if words remaining
    if clause:
        clauses.append(' '.join(clause).strip())

    # Match clauses back to word timestamps generated by whisper
    segments = []
    word_idx = 0
    for clause_text in clauses:
        clause_words = []
        cleaned_clause = clause_text.replace('“', '"').replace('”', '"').strip()
        match_text = ''
        #while not at the end of all the words and not at the end of clause
        #comparing text without spaces
        while word_idx < len(all_words) and len(match_text.replace(' ', '')) < len(cleaned_clause.replace(' ', '')):
            #get matching Word object
            w = all_words[word_idx]
            clause_words.append({
                'start': w.start,
                'end':w.end,
                'word': w.word
            })
            match_text += w.word + ' '
            word_idx += 1
        #add each clause as a segment dictionary with the clause_words dicts in "words"
        if clause_words:
            segments.append({
                'text': ' '.join(w['word'] for w in clause_words),
                'start': clause_words[0]['start'],
                'end': clause_words[-1]['end'],
                'words': clause_words
            })

    #final set of segments
    short_segments = []

    # Split each original clause segment into smaller ones or gather into larger ones
    for segment in tqdm(segments, desc="Processing Whisper segments"):
        words = segment.get("words", [])
        if not words:
            continue
        #check if segment crosses a cut and go through each word if they cross
        if crosses_cut(words[0]["start"], words[-1]["end"], cut_ranges):
            current_words = []
            current_start = words[0]["start"]
            for word in words:
                #check for which words cross a cut
                check_cuts = cross_cut(word["start"], word["end"], cut_ranges)
                if check_cuts == "in": #skip/exclude words that are in a cut
                    continue
                if type(check_cuts) is float: #if word starts in a cut but not in a cut
                    if current_words: #end the current segment and add it to the final set
                        text = ''.join([w["word"] for w in current_words]).strip()
                        short_segments.append({
                            "start": current_start,
                            "end": current_words[-1]["end"],
                            "text": text
                        })
                    #start new accumulating segment with the current word and set the start of the segment
                    #right after the end of the cut (time generated into check_cuts)
                    current_words = [word]
                    current_start = check_cuts
                    continue #move onto next word
                #if the start of the current segment and the start of the current word would cross a cut
                #(but no words are touching the cut)
                if cross_cut(current_start, word["start"], cut_ranges) == "crosses":
                    #end the current segment and add it to final set
                    if current_words:
                        text = ''.join([w["word"] for w in current_words]).strip()
                        short_segments.append({
                            "start": current_start,
                            "end": current_words[-1]["end"],
                            "text": text
                        })
                    #start the next segment with the current word and its start
                    current_words = [word]
                    current_start = word["start"]
                    continue
                #adding other words in the current clause that don't cross cuts
                last_end = word["start"]
                if not current_words:
                    current_start = word["start"]
                current_words.append(word)
                #use last_end to determine the gap between the last end of the current words array and the current word
                if len(current_words) > 1:
                    last_end = current_words[-2]["end"]
                d = word["start"] - last_end
                #check if the gap is greater than 2 seconds or there are more than 9 words in the current segment
                #if so, end current segment and add it to final set
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
            continue #move onto next clause
        #if clause doesn't cross a cut and is longer than 12 words
        if len(words) > 12:
            #split it into smaller segments
            split_segments(words, short_segments)
            continue
        #if clause doesn't cross a cut and if added to the last segment it would be less than 12
        if short_segments and len(short_segments[-1]["text"].split()) + len(words) < 12 and not crosses_cut(short_segments[-1]["start"], segment["end"], cut_ranges):
            #add it to the last segment
            short_segments[-1]["text"] += " ".join(w["word"] for w in words)
            short_segments[-1]["end"] = segment["end"]
            continue
        #else, just add it as a new segment
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

if __name__ == "__main__":
    while True:
        try:
            url = input("Enter the video page URL: ").strip()
            video_input = input("Enter the name of the video file (e.g., businessVid1.mp4) (no spaces, and make sure the file is in your downloads): ").strip()
            output_dir = Path.home() / "Downloads"
            input_file = output_dir / video_input
            if not input_file.exists():
                print(f"Error: File not found at {input_file}")
                exit(1)
            cuts = gather_cuts(url)
            transcribe_whisper(str(input_file), cuts)
            pass
        except Exception as e:
            print("An error occurred:")
            print(e)

        again = input("\nRun again? (y/n): ").strip().lower()
        if again != 'y':
            print("Goodbye!")
            break