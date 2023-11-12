import os
import json
import logging
import datetime
import re
import base64
import random
import yaml
import asyncio
import sqlite3
from datetime import date, datetime
from pathlib import Path
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from consentcrawl import utils

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CONSENT_MANAGERS_FILE = f"{MODULE_DIR}/assets/consent_managers.yml"

DEFAULT_UA_STRINGS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36 Edg/116.0.1938.81"
]


def get_extract_schema():
    return {
        "id": "STRING",
        "url": "STRING",
        "domain_name": "STRING",
        "extraction_datetime": "STRING",
        "cookies_all": "STRING",
        "cookies_no_consent": "STRING",
        "third_party_domains_all": "STRING",
        "third_party_domains_no_consent": "STRING",
        "tracking_domains_all": "STRING",
        "tracking_domains_no_consent": "STRING",
        "consent_manager": "STRING",
        "screenshot_files": "STRING",
        "meta_tags": "STRING",
        "json_ld": "STRING",
        "status": "STRING",
        "status_msg": "STRING",
    }


def get_consent_managers():
    with open(CONSENT_MANAGERS_FILE, "r") as f:
        data = yaml.safe_load(f)
        return data


async def click_consent_manager(page):
    """Retrieve list of potential consent managers and required actions to accept. Then click and return the consent manager."""
    consent_managers = get_consent_managers()

    for cmp in consent_managers:
        parent_locator = page
        locator = None

        for action in cmp["actions"]:
            if action["type"] == "iframe":
                if await parent_locator.locator(action["value"]).count() > 0:
                    parent_locator = parent_locator.frame_locator(action["value"]).first
                else:
                    continue

            elif action["type"] == "css-selector":
                if await parent_locator.locator(action["value"]).first.is_visible():
                    locator = parent_locator.locator(action["value"])
                    break

            elif action["type"] == "css-selector-list":
                for selector in action["value"]:
                    if await parent_locator.locator(selector).first.is_visible():
                        locator = parent_locator.locator(selector)
                        cmp["selector-list-item"] = selector
                        break

            elif action["type"] == "xpath":
                logging.info("XPath not implemented yet.")

        if locator is not None:
            logging.debug(
                f"Found { await locator.count()} elements for consent manager '{cmp['id']}'"
            )

            try:
                # explicit wait for navigation as some pages will reload after accepting cookies
                async with page.expect_navigation(
                    wait_until="networkidle", timeout=15000
                ):
                    await locator.first.click(delay=10)
                    logging.debug(f"Clicked consent manager '{cmp['id']}'")

                    return cmp
            except PlaywrightTimeoutError:
                logging.debug("Timeout, no navigation")
                cmp["status"] = "timeout"
                return cmp

            except Exception as e:
                error_msg = f"Error clicking consent manager '{cmp['id']}': {e}"
                logging.debug(error_msg)
                cmp["status"] = "error"
                cmp["error"] = error_msg
                return cmp

    logging.debug(f"Unable to accept cookies on: {page.url}")
    return {}


async def get_jsonld(page):
    json_ld = []
    for item in await page.locator('script[type="application/ld+json"]').all():
        contents = await item.inner_text()
        try:
            # remove potential CDATA tags
            match = re.search(
                r"//<!\[CDATA\[\s*(.*?)\s*//\]\]>", contents.strip(), re.DOTALL
            )
            if match:
                json_ld.append(json.loads(match.group(1), strict=False))
            else:
                json_ld.append(json.loads(contents.strip(), strict=False))
        except Exception as e:
            logging.debug(f"Unable to parse JSON-LD: {e}")
            json_ld.append({"raw": str(contents), "error": str(e)})

    return json_ld


async def get_meta_tags(page):
    meta_tags = {}
    for tag in await page.locator("meta[name]").all():
        try:
            meta_tags[await tag.get_attribute("name")] = await tag.get_attribute(
                "content"
            )
        except Exception as e:
            logging.debug(f"Unable to get meta tag: {e}")
    return meta_tags


async def crawl_url(
    url,
    browser,
    tracking_domains_list=[],
    screenshot=True,
    device={},
    wait_for_timeout=5000,
):
    """
    Open a new browser context with a URL and extract data about cookies and
    tracking domains before and after consent.
    Returns:
    - All third party domains requested
    - Third party domains requested before consent
    - All tracking domains (based on blocklist)
    - Tracking domains before consent
    - All cookies (name, domain, expiration in days)
    - Cookies set before consent
    - Consent manager that was used on the site
    - Screenshot of the site before consenting
    """
    output = {k: None for k in get_extract_schema().keys()}

    try:
        if not url.startswith("http"):
            url = "http://" + url

        output["url"] = url
        output["extraction_datetime"] = str(datetime.now())

        browser_context = await browser.new_context(
            user_agent=random.choice(DEFAULT_UA_STRINGS),
            viewport={"width": 1366, "height": 768},
            **device,
        )
        await browser_context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        output["domain_name"] = re.search("(?:https?://)?(?:www.)?([^/]+)", url).group(
            1
        )
        base64_url = base64.urlsafe_b64encode(
            output["domain_name"].encode("ascii")
        ).decode("ascii")

        output["id"] = base64_url

        logging.info(f"Start extracting data from domain {output['domain_name']}")

        req_urls = []

        page = await browser_context.new_page()
        page.on("request", lambda req: req_urls.append(req.url))

        await page.goto(url, wait_until="load", timeout=90000)

        # Do some mouse jiggling to keep some pages happy
        await page.wait_for_timeout(2000)
        await page.mouse.move(543, 123)
        await page.mouse.wheel(0, -123)
        await page.wait_for_timeout(
            wait_for_timeout
        )  # additional wait time just to be sure as consent managers can sometimes take a while to load

        if screenshot:
            await page.screenshot(path=f'./screenshots/screenshot_{output["id"]}.png')
            output["screenshot_files"] = [
                f'./screenshots/screenshot_{output["id"]}.png'
            ]

        logging.debug(f"Retrieving JSON-LD and meta tags on {output['domain_name']}")
        output["json_ld"] = await get_jsonld(page)
        output["meta_tags"] = await get_meta_tags(page)

        # Capture data pre-consent
        thirdparty_requests = list(
            filter(lambda req_url: not output["domain_name"] in req_url, req_urls)
        )
        output["third_party_domains_no_consent"] = list(
            set(
                map(
                    lambda r: re.search("https?://(?:www.)?([^\/]+\.[^\/]+)", r).group(
                        1
                    ),
                    thirdparty_requests,
                )
            )
        )
        output["tracking_domains_no_consent"] = list(
            set(
                [
                    re.search("[^\.]+\.[a-z]+$", d).group(0)
                    for d in output["third_party_domains_no_consent"]
                    if d in tracking_domains_list
                ]
            )
        )

        cookies = await browser_context.cookies()
        output["cookies_no_consent"] = [
            {
                "name": c["name"],
                "domain": c["domain"],
                "expires_days": (
                    date.fromtimestamp(int(c["expires"])) - date.today()
                ).days
                if int(c["expires"]) < 200000000000
                else -1,  # prevent out of range errors for the date
            }
            for c in cookies
        ]

        # try to accept full marketing consent
        logging.debug(
            f"Trying to accept full marketing consent on {output['domain_name']}"
        )
        output["consent_manager"] = await click_consent_manager(page)

        if screenshot and output["consent_manager"].get("status", "") not in [
            "error",
            "",
        ]:
            await page.screenshot(
                path=f'./screenshots/screenshot_{output["id"]}_afterconsent.png'
            )
            output["screenshot_files"].append(
                f'./screenshots/screenshot_{output["id"]}_afterconsent.png'
            )

        thirdparty_requests = list(
            filter(lambda req_url: not output["domain_name"] in req_url, req_urls)
        )
        output["third_party_domains_all"] = list(
            set(
                map(
                    lambda r: re.search("https?://(?:www.)?([^\/]+\.[^\/]+)", r).group(
                        1
                    ),
                    thirdparty_requests,
                )
            )
        )
        output["tracking_domains_all"] = list(
            set(
                [
                    re.search("[^\.]+\.[a-z]+$", d).group(0)
                    for d in output["third_party_domains_all"]
                    if d in tracking_domains_list
                ]
            )
        )

        cookies = await browser_context.cookies()

        output["cookies_all"] = [
            {
                "name": c["name"],
                "domain": c["domain"],
                "expires_days": (
                    date.fromtimestamp(int(c["expires"])) - date.today()
                ).days
                if int(c["expires"]) < 200000000000
                else -1,
            }
            for c in cookies
        ]

        await browser_context.close()

        output["status"] = "success"
        output["status_msg"] = f"Successfully extracted data from {url}"

        return output

    except Exception as e:
        error_msg = f"Error extracting data from {url}: {e}"
        logging.debug(error_msg)

        output["status"] = "error"
        output["status_msg"] = error_msg

        return output


async def crawl_batch(
    urls,
    results_function,
    batch_size=10,
    tracking_domains_list=[],
    browser_config=None,
    screenshot=False,
    **kwargs,
):
    """
    Run the crawler for multiple URLs in batches and apply a (async) function
    to the results. Additional arguments can be passed to the results function.
    """

    if not browser_config:
        browser_config = {"headless": True, "channel": "msedge"}

    async with async_playwright() as p:
        logging.debug("Starting browser")
        browser = await p.chromium.launch(**browser_config)

        for urls_batch in utils.batch(urls, batch_size):
            data = [
                crawl_url(
                    url=url,
                    browser=browser,
                    tracking_domains_list=tracking_domains_list,
                    screenshot=screenshot,
                )
                for url in urls_batch
            ]
            results = [
                r for r in await asyncio.gather(*data)
            ]  # run all urls in parallel
            logging.debug(f"Retrieved batch of {len(data)} URLs")

            await results_function(results, **kwargs)

        await browser.close()

        # return the last batch for convenience
    return results


async def crawl_single(url, tracking_domains_list=[], browser_config=None):
    """Crawl a single URL asynchronously."""

    if not browser_config:
        browser_config = {"headless": True, "channel": "msedge"}

    async with async_playwright() as p:
        logging.debug("Starting browser")
        browser = await p.chromium.launch(**browser_config)

        return await crawl_url(
            url=url, browser=browser, tracking_domains_list=tracking_domains_list
        )


async def store_crawl_results(
    data, table_name="crawl_results", file=None, results_db_file="crawl_results.db"
):
    if file is not None:
        Path.mkdir(Path(results_db_file).parent, exist_ok=True)
        with open(file, "a") as f:
            f.writelines([json.dumps(item) + "\n" for item in data])

    if results_db_file is not None:
        Path.mkdir(Path(results_db_file).parent, exist_ok=True)

        conn = sqlite3.connect(results_db_file)
        c = conn.cursor()
        c.execute(
            f"CREATE TABLE IF NOT EXISTS {table_name} ({','.join([f'{k} TEXT' for k in get_extract_schema().keys()])})"
        )
        conn.commit()

        logging.info(f"Storing {len(data)} records in database")
        c = conn.cursor()
        for d in data:
            logging.info(f"Storing {d['url']}")
            d = {
                k: json.dumps(v) if type(v) in [dict, list, tuple] else v
                for k, v in d.items()
            }
            c.execute(
                f"INSERT INTO {table_name} VALUES ({','.join(['?' for k in d.keys()])})",
                tuple(d.values()),
            )
            conn.commit()

        conn.close()
