# ccgram-hermes

[![Project Status: WIP](https://www.repostatus.org/badges/latest/wip.svg)](https://www.repostatus.org/#wip)
[![Maintenance](https://img.shields.io/badge/maintenance-passively--maintained-yellowgreen.svg)](CONTRIBUTING.md)

> Run [Hermes Agent](https://github.com/NousResearch/hermes-agent) from Telegram through [ccgram](https://github.com/alexei-led/ccgram), like any other agent.

ccgram bridges Telegram topics to coding agents running in terminal panes. It knows Claude, Codex,
Gemini, Pi and a few more. This plugin adds Hermes: its replies, tool calls and tool results come
back to the topic, its live status shows, and its approval prompts arrive as buttons you can tap.
It also adds `/silence`, one switch that mutes the whole bot.

It never edits ccgram. It installs beside it, patches seven known spots at startup, and checks each
one first. If ccgram has changed underneath it, it warns you in Telegram and refuses to start rather
than run half-working.

## Install

```bash
uv tool install git+https://github.com/4242labs/ccgram-hermes
```

Then run `ccgram-hermes` wherever you ran `ccgram`. Same flags, same `~/.ccgram` config.

It pins one ccgram version. To move to a newer ccgram, upgrade this plugin, not ccgram:

```bash
uv tool upgrade ccgram-hermes
```

ccgram's own `/upgrade` replies with that line instead of running.

## Using it

- Start `hermes` in a pane ccgram watches. The topic picks it up like any other agent.
- `/agent` and `/provider` list `hermes`. `/resume` lists your Hermes sessions for that folder.
- `/commands` lists Hermes' own slash commands.
- An approval prompt shows one button per choice. A tap on a prompt already answered does nothing.
- `/silence on` stops every message and edit, `/silence off` resumes. Nothing sent while silent is
  replayed. Bare `/silence` shows On and Off buttons.

## How it works

Hermes keeps its conversation in `~/.hermes/state.db`. The plugin opens that file read-only and
copies each bound session into a small transcript under `~/.ccgram/hermes/transcripts/`, in the
format ccgram already reads for Pi. From there ccgram does what it always does.

Nothing here writes to Hermes. If Hermes changes its database, its prompt box or its version, you get
one Telegram warning per kind per hour, and the rest keeps working.

## Going back to plain ccgram

Stop the bot, then:

```bash
ccgram-hermes release
```

That hands every Hermes topic back to ccgram. Start `ccgram` again as before.

## Contributing

Issues and pull requests are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Contributors

<!-- contributors:start -->
<!-- contributors:end -->

## License

Open source, [MIT](LICENSE).

---
If it earned its keep, [coffee is appreciated](https://buymeacoffee.com/42piratas). ☕
