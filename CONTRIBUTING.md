# Contributing to The Agentic Ledger

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/namancvs/The-Agentic-Ledger.git
cd The-Agentic-Ledger
pip install -r requirements.txt
cp .env.example .env
# Add your OPENAI_API_KEY to .env
```

## Running the Pipeline

```bash
# Full pipeline
python -m src.main

# Script only (no API cost for audio/video)
python -m src.main --dry-run

# Debug mode
python -m src.main --debug
```

## Project Structure

- `src/main.py` — Pipeline orchestrator
- `src/config.py` — All configuration (Pydantic Settings)
- `src/rss/` — RSS feed fetching and bundle management
- `src/convergence/` — Convergence engine (news → paper matching)
- `src/taxonomy/` — arXiv category system and lexicon generation
- `docs/` — Architecture documentation

## Making Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Make your changes
4. Test with `--dry-run` to validate without API costs
5. Submit a pull request

## Guidelines

- Follow existing code patterns (dataclasses, type hints, Pydantic settings)
- Keep the graceful fallback philosophy — every phase should degrade cleanly
- Document new environment variables in both `config.py` and `.env.example`
- Use the `@openai_retry` decorator for any new OpenAI API calls

## Reporting Issues

Open an issue with:
- What you expected to happen
- What actually happened
- Your Python version and OS
- Relevant logs (run with `--debug`)
