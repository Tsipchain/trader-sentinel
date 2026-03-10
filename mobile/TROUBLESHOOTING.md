# Mobile Troubleshooting

## Android Emulator warning: `ClipboardPipe: the clipboard is too large, ignoring`

If you see repeated logs like:

- `WARNING | ClipboardPipe: the clipboard is too large (...), ignoring.`

this is usually **an Android Emulator host-clipboard sync warning**, not an app crash.

### What it means

- The emulator tries to sync clipboard contents between your host OS and Android VM.
- If the host clipboard payload is very large, the emulator drops it and prints this warning.
- It does **not** indicate a Trader Sentinel logic error by itself.

### How to reduce/stop it

1. Clear your host machine clipboard (copy a short plain-text string).
2. In Android Emulator settings, disable shared clipboard / host clipboard sync.
3. Restart the emulator after changing clipboard settings.
4. Keep Metro focused on app errors (red screen / JS stack) rather than this warning.

### When to investigate app code instead

Only investigate app clipboard logic if warnings appear exactly after an explicit in-app copy action and are paired with feature breakage.
In this codebase, app clipboard usage is limited to wallet-address copy flow.
