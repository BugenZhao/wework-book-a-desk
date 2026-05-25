# Agent Skill: WeWork Book A Desk

Agent skill and dependency-free Python CLI for WeWork desk booking automation.

Detailed usage lives in [SKILL.md](SKILL.md).

## Quick Start

```bash
python3 scripts/wework_min.py auth password --username you@example.com
python3 scripts/wework_min.py locations --city Singapore
python3 scripts/wework_min.py availability --date 2026-05-26 --city Singapore --name "21 Collyer"
python3 scripts/wework_min.py book --date 2026-05-26 --city Singapore --name "21 Collyer" --dry-run
```

## Structure

```text
SKILL.md
agents/openai.yaml
scripts/wework_min.py
scripts/wework/
```

The CLI stores credentials/tokens in macOS Keychain under service `wework-book-a-desk`.
