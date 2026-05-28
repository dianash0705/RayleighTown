import argparse

from brain import ALERT_BUILDERS
from database import recompute_alerts_for_endpoint


def parse_args():
    parser = argparse.ArgumentParser(description="Run brain alert generation for one endpoint.")
    parser.add_argument("--endpointID", required=True, help="Endpoint ID to process")
    parser.add_argument(
        "--method",
        default="fourier",
        choices=sorted(ALERT_BUILDERS),
        help="Alert-building method to use",
    )
    parser.add_argument("--plot", action="store_true", help="Generate and save Fourier plots for detected events")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    created_alerts = recompute_alerts_for_endpoint(
        args.endpointID,
        method=args.method,
        plot=args.plot,
        show_progress=True,
    )
    print(f"Brain run complete for endpointID={args.endpointID}. Alerts created: {created_alerts}")
