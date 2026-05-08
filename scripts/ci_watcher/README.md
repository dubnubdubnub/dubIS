# CI Watcher

Autonomous CI failure triage running on m4-air. See `docs/superpowers/specs/2026-05-06-ci-watcher-design.md` for the design.

## Operations

- View logs: `ssh m4-air sudo tail -f /var/log/ci-watcher.log`
- View audit trail: `git log --oneline ci-watcher-log -- data/ci-watcher-log.jsonl`
- Pause: `ssh m4-air sudo launchctl unload /Library/LaunchDaemons/place.impossible.ci-watcher-listener.plist`
- Resume: `ssh m4-air sudo launchctl load /Library/LaunchDaemons/place.impossible.ci-watcher-listener.plist`
- Rotate webhook secret: see `install.sh`.
