"""Entry point. Milestone 1: lock config hash in the journal and report the worker count."""
from tradelab import config, journal, parallel


def main():
    conn = journal.connect()
    h = config.lock(conn)
    print(f"config sha256 {h}; workers {parallel.n_jobs()}")


if __name__ == "__main__":
    main()
