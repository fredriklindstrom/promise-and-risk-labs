"""Shared pieces for the Clef Mac benchmark: test set, sweep inputs, result writing."""
import json
import platform
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
TESTSET = ROOT / "data" / "testset.jsonl"
RESULTS = ROOT / "runs"
RESULTS.mkdir(exist_ok=True)

SWEEP_TARGETS = [250, 500, 2000, 8000, 32000]
SWEEP_REPS = 5
SWEEP_SKIP_SECONDS = 300  # skip remaining lengths once a single run exceeds this

ROUTING_Q = json.loads(TESTSET.read_text().splitlines()[0])["record"]["questions"]

# Realistic filler for the length sweep: a long support thread about one checkout outage.
_FILLER = [
    "Following up on the earlier message, the checkout page is still returning errors for some customers.",
    "We tried again from a different browser and the payment step hangs for about thirty seconds before failing.",
    "Our operations lead confirmed that the problem started shortly after the maintenance window on Tuesday night.",
    "Orders placed through the mobile app seem to go through, but the web checkout fails roughly one time in three.",
    "I have attached the timestamps of the failed attempts so your engineers can match them against the server logs.",
    "The error message shown to customers says that the payment could not be processed and asks them to try later.",
    "Several customers have contacted us directly and two of them say they were charged even though the order failed.",
    "Our finance team will need a list of any duplicate charges so that we can refund customers before month end.",
    "We have not changed our integration, our API keys, or our webhook configuration in the last three months.",
    "The status page still shows all systems operational, which is confusing for our support staff and customers.",
    "Could you tell us whether other merchants in the EU region are seeing the same behaviour this week?",
    "Our developer ran the diagnostic tool from your documentation and it reported a timeout on the tokenisation call.",
    "We would appreciate an estimated time for a fix, because the weekend is our busiest period for orders.",
    "If there is a workaround, such as routing payments through a different endpoint, we are happy to try it.",
    "Please also confirm whether the failed attempts count against our monthly transaction quota on the invoice.",
    "Our account manager suggested opening this ticket rather than calling, so I hope this is the right place.",
    "Customer reply: I tried three times last night and gave up, then saw two pending charges on my card this morning.",
    "Agent note: escalated to the payments team with the attached logs, awaiting their first analysis.",
    "Customer reply: thank you for the update, we will keep monitoring and send any new failures to this thread.",
    "Agent note: the payments team asked for the browser versions and any proxy or firewall in front of the store.",
    "Customer reply: our store sits behind a content delivery network but nothing has changed there recently.",
    "Agent note: a configuration change on the tokenisation service is suspected and is being rolled back in stages.",
    "Customer reply: failures dropped for an hour this afternoon and then came back at roughly the same rate.",
    "Agent note: please do not issue refunds yet, the duplicate authorisations may be released automatically.",
]


def sweep_state(tokenizer, n_state_tokens: int) -> dict:
    """A support-thread state whose rendered text is about ``n_state_tokens`` tokens."""
    parts, total, i = [], 0, 0
    while total < n_state_tokens:
        if i % len(_FILLER) == 0:
            line = f"--- Message {i // len(_FILLER) + 1} in thread #48213 ---"
        else:
            line = _FILLER[i % len(_FILLER)]
        parts.append(line)
        total += len(tokenizer.encode(line + "\n", add_special_tokens=False))
        i += 1
    return {"ticket": "\n".join(parts)}


def write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def load_testset():
    return [json.loads(l) for l in TESTSET.read_text().splitlines()]


def top(probs: dict) -> str:
    return max(probs, key=probs.get)


class Timer:
    def __init__(self, sync=None):
        self.sync = sync or (lambda: None)

    def __enter__(self):
        self.sync()
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.sync()
        self.s = time.perf_counter() - self.t0


def env_info() -> dict:
    return {"python": platform.python_version(), "platform": platform.platform()}
