# Contributing

This project is passively maintained. Issues and pull requests are read, but on no fixed schedule.

## Before you open a pull request

- Branch from `staging` and open the pull request against `staging`.
- Run the tests. They never touch a live ccgram, bot, pane or Hermes install:

  ```bash
  uv sync
  uv run pytest
  ```

- A new ccgram command must be added to the parity table in `tests/test_parity.py`. CI fails until it is.
- A new ccgram version means re-checking every patch in `ccgram_hermes/patches.py`, then bumping the pin
  in `pyproject.toml` and `CCGRAM_VERSION` together.

## Reporting a bug

Open an issue with your ccgram-hermes, ccgram and Hermes versions, and what you saw in Telegram.
Leave out bot tokens, chat ids and anything from `~/.hermes/state.db`.

Contributions are accepted under the [MIT licence](LICENSE).
