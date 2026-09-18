"""Run exactly one worker process per data directory."""

import fcntl
import logging
import signal
import threading

from feedelio.core import Core, now


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with Core() as core, (core.root / "worker.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another worker already owns this data directory.")
        # Exclusive process lock makes crash recovery safe without arbitrary lease timeouts.
        with core.db:
            core.db.execute("UPDATE jobs SET status='queued',updated=? WHERE status='running'", (now(),))
        logging.info("Feedelio worker started")
        while not stop.is_set():
            try:
                worked = core.tick()
                stop.wait(0.1 if worked else 10)
            except Exception:
                logging.exception("Worker tick failed")
                stop.wait(10)


if __name__ == "__main__":
    main()
