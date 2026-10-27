"""Main entry point for AI Auto Streamer"""
import logging
import argparse
import sys
from pathlib import Path

import uvicorn

from src.config import Config
from src.server import app


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main():
    parser = argparse.ArgumentParser(description="Run the local AI streamer and controller")
    parser.add_argument("--config", default="config.yaml", help="Configuration YAML, relative to the project root")
    args = parser.parse_args()
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("AI Auto Streamer - Phase 1")
    logger.info("=" * 60)

    config = Config.load(args.config)
    app.state.config_path = args.config

    logger.info(f"Platform: {config.platform.type}")
    if config.platform.type == "bilibili":
        logger.info(f"Bilibili Room: {config.platform.room_id}")
    logger.info(f"LLM: {config.llm.provider} / {config.llm.model}")
    logger.info(f"TTS: {config.tts.engine} / {config.tts.voice}")
    logger.info(f"Server: http://{config.server.host}:{config.server.port}")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
