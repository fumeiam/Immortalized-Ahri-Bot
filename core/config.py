from dataclasses import dataclass
import os, pathlib, logging
from dotenv import load_dotenv

DATA_DIR = pathlib.Path("data")

@dataclass
class Config:
    token: str
    app_id: str | None = None

def load_env() -> Config:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    app_id = os.getenv("APPLICATION_ID")
    if not token:
        raise RuntimeError("DISCORD_TOKEN missing in env")
    logging.getLogger().info("Loaded env (app_id=%s)", app_id)
    return Config(token=token, app_id=app_id)

def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
