import argparse

from database import recompute_alerts_for_endpoint


def parse_args():
    parser = argparse.ArgumentParser(description="Run brain alert generation for one endpoint.")
    parser.add_argument("--endpointID", required=True, help="Endpoint ID to process")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    created_alerts = recompute_alerts_for_endpoint(args.endpointID)
    print(f"Brain run complete for endpointID={args.endpointID}. Alerts created: {created_alerts}")
