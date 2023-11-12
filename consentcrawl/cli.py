import asyncio
import os
import json
import logging
import argparse
import sys
from consentcrawl import crawl, utils, blocklists


async def process_urls(
    urls,
    batch_size,
    tracking_domains_list,
    headless=True,
    screenshot=True,
    results_db_file="crawl_results.db",
):
    """
    Start the Playwright browser, run the URLs to test in batches asynchronously
    and write the data to a file.
    """

    return await crawl.crawl_batch(
        urls=urls,
        batch_size=batch_size,
        results_function=crawl.store_crawl_results,
        tracking_domains_list=tracking_domains_list,
        browser_config={"headless": headless, "channel": "msedge"},
        results_db_file=results_db_file,
        screenshot=screenshot,
    )


def cli():
    parser = argparse.ArgumentParser()

    parser.add_argument("url", help="URL or file with URLs to test")
    parser.add_argument(
        "--debug", default=False, action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--headless",
        default=True,
        type=utils.string_to_boolean,
        const=False,
        nargs="?",
        help="Run browser in headless mode (yes/no)",
    )
    parser.add_argument(
        "--screenshot",
        default=False,
        action="store_true",
        help="Take screenshots of each page before and after consent is given (if consent manager is detected)",
    )
    parser.add_argument(
        "--bootstrap",
        default=False,
        action="store_true",
        help="Force bootstrap (refresh) of blocklists",
    )
    parser.add_argument(
        "--batch_size",
        "-b",
        default=15,
        type=int,
        help="Number of URLs (and browser windows) to run in each batch. Default: 15, increase or decrease depending on your system capacity.",
    )
    parser.add_argument(
        "--show_output",
        "-o",
        default=False,
        action="store_true",
        help="Show output of the last results in terminal (max 25 results)",
    )
    parser.add_argument(
        "--db_file",
        "-db",
        default="crawl_results.db",
        help="Path to crawl results and blocklist database",
    )
    parser.add_argument(
        "--blocklists", "-bf", default=None, help="Path to custom blocklists file"
    )

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    if not args.db_file.endswith(".db"):
        args.db_file = args.db_file + ".db"

    if args.blocklists != None:
        if not os.path.isfile(args.blocklists):
            logging.error(f"Blocklists file not found: {args.blocklists}")
            sys.exit(1)

        if not any(
            [args.blocklists.endswith(".yaml"), args.blocklists.endswith(".yml")]
        ):
            logging.error(f"Blocklists file must be a YAML file: {args.blocklists}")
            sys.exit(1)

    if not os.path.isdir("screenshots") and args.screenshot == True:
        os.mkdir("screenshots")

    # List of URLs to test
    if args.url.endswith(".txt"):
        with open(args.url, "r") as f:
            urls = list(
                set(
                    [
                        l.strip().lower()
                        for l in set(f.readlines())
                        if len(l) > 0 and not l.startswith("#")
                    ]
                )
            )

    elif args.url != "":
        urls = args.url.split(",")
    else:
        logging.error("No URL or valid .txt file with URLs to test")

    # Bootstrap blocklists
    blockers = blocklists.Blocklists(
        db_file=args.db_file,
        source_file=args.blocklists,
        force_bootstrap=args.bootstrap,
    )

    results = asyncio.run(
        process_urls(
            urls=urls,
            batch_size=args.batch_size,
            tracking_domains_list=blockers.get_domains(),
            headless=args.headless,
            screenshot=args.screenshot,
            results_db_file=args.db_file,
        )
    )

    if args.show_output and len(results) < 25:
        sys.stdout.write(json.dumps(results, indent=2))

    sys.exit(0)
