# email-podcast-brief

Polls a Gmail inbox for emails matching a subject line, converts them to speech via a Kokoro TTS server, and serves the results as a private podcast RSS feed.

## How it works

- `main.py` - polls Gmail via IMAP on a schedule, extracts a headline + body, sends text to Kokoro TTS, writes the audio file and updates the RSS feed (`/data/feed.xml`).
- `server.py` - serves the feed and audio files over HTTP, with an admin UI (password-gated) and optional Basic Auth on the feed itself.
- `entrypoint.sh` - copies cover art into `/data`, starts both processes.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GMAIL_USER` | yes | - | Gmail address to poll |
| `GMAIL_PASSWORD` | yes | - | Gmail app password (not your regular password) |
| `SUBJECT_FILTER` | no | `""` | Only process emails whose subject matches this |
| `POLL_WINDOW_START` | no | `05:00` | Start of daily polling window (HH:MM) |
| `POLL_WINDOW_END` | no | `08:00` | End of daily polling window (HH:MM) |
| `POLL_INTERVAL_MIN` | no | `15` | Minutes between inbox polls |
| `FEED_BASE_URL` | no | `http://localhost:8080` | Public URL the feed/audio are served from |
| `PODCAST_TITLE` | no | `My Email Podcast` | Podcast feed title |
| `PODCAST_DESC` | no | `Auto-generated podcast from email` | Podcast feed description |
| `PODCAST_AUTHOR` | no | `$GMAIL_USER` | Podcast feed author |
| `AUDIO_OUT_DIR` | no | `/data/audio` | Where generated audio files are written |
| `AUDIO_TOKEN` | no | `""` | Optional token appended to audio URLs |
| `KOKORO_URL` | no | `http://192.168.111.189:8815` | Base URL of the Kokoro TTS server |
| `KOKORO_VOICE` | no | `af_heart` | Kokoro voice preset |
| `HTTP_PORT` | no | `8080` | Port the HTTP server listens on |
| `TZ` | no | - | Container timezone |
| `ADMIN_PASSWORD` | no | `changeme` | Password for the admin UI - change this |
| `FEED_USER` | no | `podcast` | Basic-auth username for the feed |
| `FEED_PASSWORD` | no | `""` | Basic-auth password for the feed (leave blank to disable auth) |

## Volumes

- `/data` - persistent state: `feed.xml`, `processed.json` (dedup tracking), `audio/` (generated MP3s), `cover.png`

## Multiple instances

This image is generic - run multiple containers from it with different `GMAIL_USER`/`SUBJECT_FILTER`/`FEED_BASE_URL`/data volumes to run separate podcast feeds off separate inbox filters.
