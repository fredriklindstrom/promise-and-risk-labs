"""Human acknowledgement (deliberately not an MCP tool).

  python -m minisoc.ack                       list open alerts
  python -m minisoc.ack 12 "that was me"      acknowledge alert 12
  python -m minisoc.ack 12 "my laptop" --trust-ip   also add the alert's source IP to the baseline
"""
import argparse

from . import config, store, watcher


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("alert_id", nargs="?", type=int)
    ap.add_argument("note", nargs="?", default="")
    ap.add_argument("--trust-ip", action="store_true", help="add the alert's source IP to the known-normal admin IPs")
    a = ap.parse_args()
    db = store.connect()
    if a.alert_id is None:
        for x in store.alerts(db, "open", limit=1000):
            title = "".join(ch if ch.isprintable() else "?" for ch in x["title"])  # no terminal escapes
            print(f"{x['id']:>4}  {x['severity']:6}  {title}")
    else:
        alert = store.get_alert(db, a.alert_id)
        if not alert or not store.ack(db, a.alert_id, a.note):
            print(f"no open alert {a.alert_id}")
        else:
            store.add_verdict(db, alert, "human", watcher.rule_version(), verdict="acknowledged",
                              human_decision=a.note or "acknowledged")
            ip = ((alert.get("detail") or {}).get("event") or {}).get("source_ip")
            if a.trust_ip and ip:
                store.baseline_add(db, "admin_ip", ip, a.note or f"trusted via alert {a.alert_id}")
                print(f"added {ip} to known admin addresses")
            print(f"acknowledged {a.alert_id}")
        db.commit()
        watcher.write_state(db, config.load())
    db.close()


if __name__ == "__main__":
    main()
