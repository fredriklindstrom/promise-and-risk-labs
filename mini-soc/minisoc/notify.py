"""macOS notifications. Text goes to osascript as argv, never spliced into the script, because
alert titles can carry text that originated outside this machine."""
import subprocess

SCRIPT = ("on run argv\n"
          "display notification (item 1 of argv) with title (item 2 of argv) subtitle (item 3 of argv)\n"
          "end run")


def mac(title, subtitle, body):
    """Returns True once the notification was handed to macOS. '--' ends osascript's own option
    parsing, so a body starting with '-e' can't be read as more script."""
    try:
        r = subprocess.run(["osascript", "-e", SCRIPT, "--", body[:240], title[:80], subtitle[:120]],
                           check=False, capture_output=True, timeout=15)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False
