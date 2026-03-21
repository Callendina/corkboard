#!/usr/bin/env python3
import uvicorn
from corkboard.config import load_config

config = load_config()
uvicorn.run("corkboard.app:app", host=config.host, port=config.port, log_level="info")
