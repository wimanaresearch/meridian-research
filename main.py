import argparse
from dotenv import load_dotenv

from agents.analyst.collector import collect
from agents.analyst.analyzer import analyze
from publishers.discord import publish


load_dotenv()


def run(topics: list[str], symbols: list[str], dry_run: bool = False) -> None:
    print(f"Collecting data for topics={topics}, symbols={symbols}...")
    data = collect(topics, symbols)

    print("Analyzing with Claude...")
    report = analyze(data)

    print("\n--- Report ---")
    print(report)
    print("--- End Report ---\n")

    if dry_run:
        print("[dry-run] Skipping Discord publish.")
    else:
        print("Publishing to Discord...")
        publish(report)
        print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Market Intelligence Bot")
    parser.add_argument(
        "--topics",
        nargs="+",
        default=["AI stocks", "semiconductor industry"],
        help="News search topics",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["NVDA", "MSFT", "AAPL"],
        help="Stock ticker symbols",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print report without sending to Discord",
    )
    args = parser.parse_args()
    run(args.topics, args.symbols, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
