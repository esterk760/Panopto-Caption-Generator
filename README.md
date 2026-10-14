# Panopto Caption Generator

A Windows Python utility that signs in to Panopto, retrieves a
recording's session information and edit cuts, transcribes the recording
with `faster-whisper`, and saves captions as an `.srt` subtitle file.


## The Inspiring Issue

Panopto automated caption segments would disappear if they crossed over a cut.
Manually editing around cuts was very time-consuming, so I worked on making my own
caption generator that takes into consideration edited cuts. Also, Panopto
automated caption segments are at times very long, so I added logic in my script
to make generated segments shorter.


## Features

-   Authenticates to Panopto through OAuth 2.0 and your organization's
    single sign-on (SSO).
-   Accepts a Panopto session ID from the recording URL.
-   Retrieves the recording's stream and edit-cut information.
-   Uses FFmpeg to extract audio when the stream is delivered as a
    manifest rather than a direct MP4 download.
-   Transcribes speech locally with `faster-whisper` using the small
    model on CPU with INT8 computation.
-   Uses the retrieved cut information when preparing caption segments.
-   Saves the finished `.srt` file to the current Windows user's
    **Downloads** folder.
-   Beeps when a processing run finishes and can run again for another
    recording.

## Building Process

I began this project the summer of 2025 on my own initiative as
a student worker at my university's distance learning team. 
Since I wanted to build the project as quickly as I could to save time,
I used ChatGPT to get the beginning code template and to learn 
about the necessary tools for my project. I then troubleshooted, 
added more functionality, and revised code to work for my intended purposes.
Multiple versions were made and used since then. It began with learning 
and using Selenium and web scraping to attain cuts from the site, which was
slow and unreliable. In the summer of 2026, I changed the method to use
the Panopto API, making the process faster and the application easier to use.

## Results




