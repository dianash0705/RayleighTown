import argparse

from database import recompute_alerts_for_endpoint


def parse_args():
    parser = argparse.ArgumentParser(description="Run brain alert generation for one endpoint.")
    parser.add_argument("--endpointID", required=True, help="Endpoint ID to process")
    parser.add_argument("--plot", action="store_true", help="Generate and save Fourier plots for detected events")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    created_alerts = recompute_alerts_for_endpoint(args.endpointID, plot=args.plot)
    print(f"Brain run complete for endpointID={args.endpointID}. Alerts created: {created_alerts}")
