"""Assign existing (unowned) endpoints to an organization.

Once auth is enabled, dashboard/alerts/entities only show data for endpoints that
belong to the logged-in account's organization. Demo data imported before auth has
no organization, so it is invisible. This helper lets you adopt that legacy data
into an organization for testing.

Usage (from the backend directory):

    python claim_endpoints.py --organization "Acme Security"
    python claim_endpoints.py --organization "Acme Security" --endpoint 123 456

With no --endpoint values, every endpoint that has no organization is claimed.

Note: claimed endpoints do not get an upload secret, so they remain view-only.
Register a fresh endpoint from the admin page if you need the agent to upload.
"""

import argparse

from database import connect_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Adopt unowned endpoints into an organization.")
    parser.add_argument("--organization", required=True, help="Target organization name.")
    parser.add_argument("--endpoint", nargs="*", default=None, help="Specific endpoint IDs to claim.")
    args = parser.parse_args()

    conn = connect_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT organizationID FROM organizations WHERE name = ? COLLATE NOCASE",
        (args.organization,),
    )
    row = cursor.fetchone()
    if row is None:
        conn.close()
        raise SystemExit(f"No organization named {args.organization!r}. Register it first via the web UI.")
    organization_id = int(row[0])

    if args.endpoint:
        placeholders = ", ".join("?" for _ in args.endpoint)
        cursor.execute(
            f"UPDATE endpoints SET organizationID = ? WHERE endpointID IN ({placeholders})",
            (organization_id, *args.endpoint),
        )
    else:
        cursor.execute(
            "UPDATE endpoints SET organizationID = ? WHERE organizationID IS NULL",
            (organization_id,),
        )

    claimed = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"Claimed {claimed} endpoint(s) into organization {args.organization!r} (id {organization_id}).")


if __name__ == "__main__":
    main()
