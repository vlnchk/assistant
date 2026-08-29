import os


# The production logger writes to /app/data. Tests run without touching the
# user's application data or requiring the Docker filesystem layout.
os.environ.setdefault("BOT_LOG_FILE", "/tmp/edujobs-tests.log")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
