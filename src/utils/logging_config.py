"""Logging configuration with optional GCP Cloud Logging integration."""

import logging
import sys
from typing import Optional


def setup_logging(
    level: str = "INFO",
    gcp_project_id: Optional[str] = None,
) -> None:
    """
    Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        gcp_project_id: Optional GCP project ID for Cloud Logging integration
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Base format for local logging
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)

    # GCP Cloud Logging integration
    if gcp_project_id:
        try:
            import google.cloud.logging

            client = google.cloud.logging.Client(project=gcp_project_id)
            client.setup_logging(log_level=log_level)
            logging.info(f"GCP Cloud Logging enabled for project: {gcp_project_id}")
        except ImportError:
            logging.warning(
                "google-cloud-logging not installed. Skipping GCP integration."
            )
        except Exception as e:
            logging.warning(f"Failed to setup GCP Cloud Logging: {e}")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)
