import os

from dotenv import load_dotenv


def load_env():
    # Try local .env first
    local_env = os.path.join(os.getcwd(), ".env")

    # Or environment-only mode
    external_env = os.getenv("EXTERNAL_ENV_PATH")

    if os.path.exists(local_env):
        load_dotenv(local_env)
    elif external_env:
        load_dotenv(external_env)
    else:
        print("⚠️ No .env file loaded (using system environment variables only).")
