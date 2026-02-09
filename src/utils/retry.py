"""Retry decorators for external API calls."""

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import logging

from openai import (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

logger = logging.getLogger(__name__)

# OpenAI API retry decorator
openai_retry = retry(
    retry=retry_if_exception_type(
        (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
    ),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
