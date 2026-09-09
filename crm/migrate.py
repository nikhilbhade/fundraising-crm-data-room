"""Initialize or migrate the configured database for a deployment."""

from __future__ import annotations

from . import database as db


def main() -> None:
    connection = db.bootstrap()
    try:
        firms = db.q(connection, "SELECT COUNT(*) AS count FROM firms").iloc[0]["count"]
        print(f"Database ready: {db.backend_label(connection)}; {firms} firms")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
