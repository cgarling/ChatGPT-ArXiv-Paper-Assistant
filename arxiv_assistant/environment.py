import configparser
import feedparser
import os
from datetime import UTC, datetime

from arxiv_assistant.utils.io import create_dir


def parse_authors(lines):
    # parse the comma-separated author list, ignoring lines that are empty and starting with #
    author_ids = []
    authors = []
    for line in lines:
        if line.startswith("#"):
            continue
        if not line.strip():
            continue
        author_split = line.split(",")
        author_ids.append(author_split[1].strip())
        authors.append(author_split[0].strip())
    return authors, author_ids


# load config.ini
CONFIG = configparser.ConfigParser()
CONFIG.read("configs/config.ini")

print({section: dict(CONFIG[section]) for section in CONFIG.sections()})
print(f"###################################################################")

# load authors.txt
with open("configs/authors.txt", "r", encoding="utf-8") as fopen:
    author_names, author_ids = parse_authors(fopen.readlines())
AUTHOR_ID_SET = set(author_ids)

# load prompts
with open("prompts/system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()
with open("prompts/paper_topics.txt", "r", encoding="utf-8") as f:
    TOPIC_PROMPT = f.read()
with open("prompts/score_criteria.txt", "r", encoding="utf-8") as f:
    SCORE_PROMPT = f.read()
with open("prompts/postfix_prompt_title.txt", "r", encoding="utf-8") as f:
    POSTFIX_PROMPT_TITLE = f.read()
with open("prompts/postfix_prompt_abstract.txt", "r", encoding="utf-8") as f:
    POSTFIX_PROMPT_ABSTRACT = f.read()

# keys
S2_API_KEY = os.environ.get("S2_KEY")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
GOOGLE_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEEPSEEK_OPENAI_BASE_URL = "https://api.deepseek.com"
SLACK_KEY = os.environ.get("SLACK_KEY")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID")

print(f"S2_API_KEY set: {S2_API_KEY is not None}")
print(f"GOOGLE_API_KEY set: {GOOGLE_API_KEY is not None}")
print(f"DEEPSEEK_API_KEY set: {DEEPSEEK_API_KEY is not None}")
print(f"SLACK_KEY set: {SLACK_KEY is not None}")
print(f"SLACK_CHANNEL_ID set: {SLACK_CHANNEL_ID is not None}")

# now time
try:
    # get from ArXiv
    feed = feedparser.parse("https://export.arxiv.org/rss/cs.LG")  # use the cs.LG area
    if len(feed.entries) > 0:
        # Example `feed.published`: "Tue, 18 Feb 2025 00:00:00 -0500"
        parsed_time = datetime.strptime(feed.entries[0].published, "%a, %d %b %Y %H:%M:%S %z")
        NOW_TIME = parsed_time
        NOW_YEAR = int(NOW_TIME.strftime("%Y"))
        NOW_MONTH = int(NOW_TIME.strftime("%m"))
        NOW_DAY = int(NOW_TIME.strftime("%d"))
    else:
        raise ValueError("Feed does not contain any entries")
except Exception as e:
    # use local time
    NOW_TIME = datetime.now(UTC)
    NOW_YEAR = int(NOW_TIME.strftime("%Y"))
    NOW_MONTH = int(NOW_TIME.strftime("%m"))
    NOW_DAY = int(NOW_TIME.strftime("%d"))

print(f"NOW_YEAR: {NOW_YEAR}")
print(f"NOW_MONTH: {NOW_MONTH}")
print(f"NOW_DAY: {NOW_DAY}")

def configure_output_date(output_date):
    global OUTPUT_DEBUG_DIR, OUTPUT_DEBUG_FILE_FORMAT, OUTPUT_MD_DIR, OUTPUT_MD_FILE_FORMAT, OUTPUT_JSON_DIR, OUTPUT_JSON_FILE_FORMAT

    year, month, day = output_date
    month_string = f"{year}-{month:02d}"
    date_string = f"{month_string}-{day:02d}"
    output_path = CONFIG["OUTPUT"]["output_path"]

    OUTPUT_DEBUG_DIR = os.path.join(output_path, "debug", month_string, date_string)
    OUTPUT_DEBUG_FILE_FORMAT = os.path.join(OUTPUT_DEBUG_DIR, "{}")
    OUTPUT_MD_DIR = os.path.join(output_path, "md", month_string)
    OUTPUT_MD_FILE_FORMAT = os.path.join(OUTPUT_MD_DIR, f"{date_string}-{{}}")
    OUTPUT_JSON_DIR = os.path.join(output_path, "json", month_string)
    OUTPUT_JSON_FILE_FORMAT = os.path.join(OUTPUT_JSON_DIR, f"{date_string}-{{}}")
    for directory in (OUTPUT_DEBUG_DIR, OUTPUT_MD_DIR, OUTPUT_JSON_DIR):
        create_dir(directory)


configure_output_date((NOW_YEAR, NOW_MONTH, NOW_DAY))
print(f"OUTPUT_DEBUG_DIR: {OUTPUT_DEBUG_DIR}")
print(f"OUTPUT_DEBUG_FILE_FORMAT: {OUTPUT_DEBUG_FILE_FORMAT}")
print(f"OUTPUT_MD_DIR: {OUTPUT_MD_DIR}")
print(f"OUTPUT_MD_FILE_FORMAT: {OUTPUT_MD_FILE_FORMAT}")
print(f"OUTPUT_JSON_DIR: {OUTPUT_JSON_DIR}")
print(f"OUTPUT_JSON_FILE_FORMAT: {OUTPUT_JSON_FILE_FORMAT}")
print(f"###################################################################")
