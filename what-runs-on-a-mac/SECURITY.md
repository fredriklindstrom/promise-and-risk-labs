# Security

This lab runs downloaded AI models and local servers on your Mac, and one script runs `powermetrics` with `sudo`.
If you find a way to make it run code it shouldn't, write outside its folder, escalate privileges, expose a server
beyond 127.0.0.1, or skip a hash check, please report it privately rather than in a public issue:

- GitHub: open a private security advisory on this repository ("Security" tab → "Report a vulnerability").

Please include the file and line, what an attacker needs, and what they gain. You'll get an answer within a week.

Out of scope: results differing between Macs, and anything that requires an attacker who already runs code as your
user (they can already do everything this harness can).
