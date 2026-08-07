#!/usr/bin/env python3
import imaplib, email, email.header, hashlib, json, logging, os, re, subprocess, schedule, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from feedgen.feed import FeedGenerator

GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_PASSWORD = os.environ["GMAIL_PASSWORD"]
SUBJECT_FILTER = os.environ.get("SUBJECT_FILTER", "")
POLL_WINDOW_START = os.environ.get("POLL_WINDOW_START", "05:00")  # HH:MM
POLL_WINDOW_END   = os.environ.get("POLL_WINDOW_END",   "08:00")  # HH:MM
POLL_INTERVAL_MIN = int(os.environ.get("POLL_INTERVAL_MIN", "15"))   # minutes
FEED_BASE_URL  = os.environ.get("FEED_BASE_URL", "http://localhost:8080")
PODCAST_TITLE  = os.environ.get("PODCAST_TITLE", "My Email Podcast")
PODCAST_DESC   = os.environ.get("PODCAST_DESC", "Auto-generated podcast from email")
PODCAST_AUTHOR = os.environ.get("PODCAST_AUTHOR", GMAIL_USER)
AUDIO_OUT_DIR  = Path(os.environ["AUDIO_OUT_DIR"]) if os.environ.get("AUDIO_OUT_DIR") else None
FEED_IMAGE_URL = f"{FEED_BASE_URL}/cover.png"
AUDIO_TOKEN    = os.environ.get("AUDIO_TOKEN", "")

# Kokoro TTS container
KOKORO_URL   = os.environ.get("KOKORO_URL", "http://192.168.111.189:8815")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")

DATA_DIR   = Path("/data")
AUDIO_DIR  = DATA_DIR / "audio"
STATE_FILE = DATA_DIR / "processed.json"
FEED_FILE  = DATA_DIR / "feed.xml"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def extract_headline(body: str) -> str:
    """Extract title from -----HEADLINE----- block at end of email."""
    match = re.search(r'[-]{3,}HEADLINE[-]*[ \t]*\n?([^\n]+)', body, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""

def strip_headline_block(body: str) -> str:
    """Remove the -----HEADLINE----- block and everything after it from body."""
    return re.split(r'[-]{3,}HEADLINE[-]*', body, flags=re.IGNORECASE)[0].strip()

def clean_for_tts(text: str) -> str:
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'\S+@\S+\.\S+', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'\bvs\.\b', 'versus', text, flags=re.IGNORECASE)
    text = re.sub(r'\betc\.\b', 'et cetera', text, flags=re.IGNORECASE)
    text = re.sub(r'\be\.g\.\b', 'for example', text, flags=re.IGNORECASE)
    text = re.sub(r'\bi\.e\.\b', 'that is', text, flags=re.IGNORECASE)
    text = re.sub(r'^\s*[-•*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def generate_audio(text: str, output_path: Path):
    """Call Kokoro container's OpenAI-compatible TTS endpoint, save MP3."""
    text = clean_for_tts(text)
    log.info(f"Calling Kokoro TTS at {KOKORO_URL} ({len(text)} chars)...")

    payload = json.dumps({
        "model": "kokoro",
        "input": text,
        "voice": KOKORO_VOICE,
        "response_format": "mp3",
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{KOKORO_URL}/v1/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Long timeout — big emails take time
    with urllib.request.urlopen(req, timeout=300) as resp:
        audio_bytes = resp.read()

    output_path.write_bytes(audio_bytes)
    log.info(f"Audio ready: {output_path.name} ({len(audio_bytes)} bytes)")

def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"processed": [], "episodes": []}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def extract_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                body += part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        body = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
    return re.sub(r'\n{3,}', '\n\n', re.sub(r'\r\n', '\n', body)).strip()

def fetch_emails():
    results = []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(GMAIL_USER, GMAIL_PASSWORD)
        mail.select("inbox")
        query = f'(UNSEEN SUBJECT "{SUBJECT_FILTER}")' if SUBJECT_FILTER else "UNSEEN"
        _, data = mail.search(None, query)
        ids = data[0].split()
        log.info(f"Found {len(ids)} matching unseen email(s)")
        for uid in ids:
            _, msg_data = mail.fetch(uid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subj, enc = email.header.decode_header(msg.get("Subject", "No Subject"))[0]
            if isinstance(subj, bytes):
                subj = subj.decode(enc or "utf-8", errors="replace")
            msg_id = msg.get("Message-ID", str(uid))
            results.append({
                "uid": uid.decode(),
                "uid_hash": hashlib.md5(msg_id.encode()).hexdigest(),
                "subject": subj,
                "date": msg.get("Date", ""),
                "body": extract_body(msg),
            })
        mail.logout()
    except Exception as e:
        log.error(f"IMAP error: {e}")
    return results

def rebuild_feed(state):
    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.id(FEED_BASE_URL); fg.title(PODCAST_TITLE)
    fg.author({"name": PODCAST_AUTHOR, "email": GMAIL_USER})
    fg.link(href=FEED_BASE_URL, rel="alternate")
    fg.link(href=f"{FEED_BASE_URL}/feed.xml", rel="self")
    fg.description(PODCAST_DESC); fg.language("en")
    fg.podcast.itunes_author(PODCAST_AUTHOR)
    fg.podcast.itunes_explicit("no")
    fg.podcast.itunes_image(FEED_IMAGE_URL)
    for ep in sorted(state["episodes"], key=lambda x: x["timestamp"], reverse=True):
        fe = fg.add_entry()
        fe.id(ep["uid_hash"]); fe.title(ep["subject"]); fe.description(ep["subject"])
        fe.published(ep["timestamp"]); fe.podcast.itunes_duration(ep.get("duration", "00:00"))
        token_suffix = f"?token={AUDIO_TOKEN}" if AUDIO_TOKEN else ""
        fe.enclosure(f"{FEED_BASE_URL}/audio/{ep['filename']}{token_suffix}", str(ep.get("filesize", 0)), "audio/mpeg")
    fg.rss_file(str(FEED_FILE))
    log.info(f"Feed updated — {len(state['episodes'])} episode(s)")

def process():
    log.info("=== Email check ===")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    if AUDIO_OUT_DIR:
        AUDIO_OUT_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    done = set(state["processed"])
    new = 0

    for em in fetch_emails():
        if em["uid_hash"] in done:
            log.info(f"Skip: {em['subject']}"); continue
        if not em["body"].strip():
            log.warning(f"Empty body, skipping: {em['subject']}"); continue

        headline = extract_headline(em["body"])
        tts_body = strip_headline_block(em["body"])
        episode_title = headline if headline else em["subject"]
        log.info(f"Processing: {episode_title}")
        filename = f"{em['uid_hash']}.mp3"

        if AUDIO_OUT_DIR:
            audio_path = AUDIO_OUT_DIR / filename
            link_path = AUDIO_DIR / filename
        else:
            audio_path = AUDIO_DIR / filename
            link_path = None

        try:
            generate_audio(f"{episode_title}.\n\n{tts_body}", audio_path)
            if link_path and not link_path.exists():
                link_path.symlink_to(audio_path)
        except Exception as e:
            log.error(f"Audio generation failed for '{em['subject']}': {e}")
            continue

        try:
            from email.utils import parsedate_to_datetime
            pub_dt = parsedate_to_datetime(em["date"])
        except Exception:
            pub_dt = datetime.now(timezone.utc)

        state["processed"].append(em["uid_hash"])
        state["episodes"].append({
            "uid_hash": em["uid_hash"],
            "subject": episode_title,
            "filename": filename,
            "filesize": audio_path.stat().st_size,
            "timestamp": pub_dt.isoformat(),
            "duration": "00:00",
        })
        new += 1

    if new:
        save_state(state); rebuild_feed(state); log.info(f"Added {new} episode(s)")
    else:
        log.info("No new emails")

if __name__ == "__main__":
    log.info(f"Starting | filter='{SUBJECT_FILTER}' | window={POLL_WINDOW_START}-{POLL_WINDOW_END} every {POLL_INTERVAL_MIN}min | feed={FEED_BASE_URL}/feed.xml")
    log.info(f"Kokoro TTS: {KOKORO_URL} | voice={KOKORO_VOICE}")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    if AUDIO_OUT_DIR:
        AUDIO_OUT_DIR.mkdir(parents=True, exist_ok=True)
    def maybe_process():
        """Only run if current time is within the polling window."""
        now = datetime.now()
        start_h, start_m = map(int, POLL_WINDOW_START.split(":"))
        end_h,   end_m   = map(int, POLL_WINDOW_END.split(":"))
        window_start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        window_end   = now.replace(hour=end_h,   minute=end_m,   second=0, microsecond=0)
        if window_start <= now <= window_end:
            process()
        else:
            log.info(f"Outside poll window ({POLL_WINDOW_START}–{POLL_WINDOW_END}), skipping")

    # Always poll on startup regardless of time window
    process()

    schedule.every(POLL_INTERVAL_MIN).minutes.do(maybe_process)
    log.info(f"Polling every {POLL_INTERVAL_MIN} min, active {POLL_WINDOW_START}–{POLL_WINDOW_END}")
    while True:
        schedule.run_pending()
        time.sleep(30)
