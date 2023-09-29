import os, json, logging, datetime, re, base64, random, yaml
from datetime import date, datetime
import requests
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CONSENT_MANAGERS_FILE = f"{MODULE_DIR}/assets/consent_managers.yml"

DEFAULT_UA_STRINGS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36 Edg/116.0.1938.81",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36"
]

def get_extract_schema():
    return {
            "id" : "STRING",
            "url" : "STRING",
            "domain_name" : "STRING",
            "extraction_datetime" : "STRING",
            "cookies_all" : "STRING",
            "cookies_no_consent" : "STRING",
            "third_party_domains_all" : "STRING",
            "third_party_domains_no_consent" : "STRING",
            "tracking_domains_all" : "STRING",
            "tracking_domains_no_consent" : "STRING",
            "consent_manager" : "STRING",
            "screenshot_files" : "STRING",
            "meta_tags" : "STRING",
            "json_ld" : "STRING",
            "status" : "STRING",
            "status_msg" : "STRING"
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
                    parent_locator = parent_locator.frame_locator(action["value"])
                else:
                    continue

            elif action["type"] == "css-selector":
                if await parent_locator.locator(action['value']).is_visible():
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
            logging.debug(f"Found { await locator.count()} elements for consent manager '{cmp['id']}'")

            try:
                # explicit wait for navigation as some pages will reload after accepting cookies
                async with page.expect_navigation(wait_until="networkidle", timeout=15000):
                    await locator.click(delay=10)
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
        try:
            json_ld.append(json.loads(await item.inner_text()))
        except Exception as e: 
            logging.debug(f"Unable to parse JSON-LD: {e}")
    return json_ld

async def get_meta_tags(page):
    meta_tags = {}
    for tag in await page.locator('meta[name]').all():
        try:
            meta_tags[await tag.get_attribute('name')] = await tag.get_attribute('content')
        except Exception as e:
            logging.debug(f"Unable to get meta tag: {e}")
    return meta_tags

async def crawl_url(url, browser, tracking_domains_list, screenshot=True):
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

        browser_context = await browser.new_context(user_agent=random.choice(DEFAULT_UA_STRINGS))
        await browser_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        output["domain_name"] = re.search("(?:https?://)?(?:www.)?([^/]+)", url).group(1)
        base64_url = base64.urlsafe_b64encode(output["domain_name"].encode('ascii')).decode('ascii')
        
        output["id"] = base64_url

        logging.info(f"Start extracting data from domain {output['domain_name']}")
        
        req_urls = []
        
        page = await browser_context.new_page()
        page.on("request", lambda req: req_urls.append(req.url))
        
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(3000) # additional wait time just to be sure as consent managers can sometimes take a while to load

        if screenshot:
            await page.screenshot(path=f'./screenshots/screenshot_{output["id"]}.png')
            output["screenshot_files"] = [f'./screenshots/screenshot_{output["id"]}.png']

        logging.debug(f"Retrieving JSON-LD and meta tags on {output['domain_name']}")
        output["json_ld"] = await get_jsonld(page)
        output["meta_tags"] = await get_meta_tags(page)

        # Capture data pre-consent
        thirdparty_requests = list(filter(lambda req_url: not output["domain_name"] in req_url, req_urls))
        output["third_party_domains_no_consent"] = list(set(map(lambda r: re.search("https?://(?:www.)?([^\/]+\.[^\/]+)", r).group(1), thirdparty_requests)))
        output["tracking_domains_no_consent"] = list(set([re.search("[^\.]+\.[a-z]+$", d).group(0) for d in output["third_party_domains_no_consent"] if d in tracking_domains_list]))

        cookies = await browser_context.cookies()
        output["cookies_no_consent"] = [{
            "name" : c["name"],
            "domain" : c["domain"],
            "expires_days" : (date.fromtimestamp(int(c["expires"])) - date.today()).days if int(c["expires"]) < 200000000000 else -1 # prevent out of range errors for the date
        } for c in cookies]

        # try to accept full marketing consent
        logging.debug(f"Trying to accept full marketing consent on {output['domain_name']}")
        output["consent_manager"] = await click_consent_manager(page)

        if screenshot and output["consent_manager"].get("status", "") not in ["error", ""]:
            await page.screenshot(path=f'./screenshots/screenshot_{output["id"]}_afterconsent.png')
            output["screenshot_files"].append(f'./screenshots/screenshot_{output["id"]}_afterconsent.png')

        thirdparty_requests = list(filter(lambda req_url: not output['domain_name'] in req_url, req_urls))
        output["third_party_domains_all"] = list(set(map(lambda r: re.search("https?://(?:www.)?([^\/]+\.[^\/]+)", r).group(1), thirdparty_requests)))
        output["tracking_domains_all"] = list(set([re.search("[^\.]+\.[a-z]+$", d).group(0) for d in output["third_party_domains_all"] if d in tracking_domains_list]))

        cookies = await browser_context.cookies()

        output["cookies_all"] = [{
            "name" : c["name"],
            "domain" : c["domain"],
            "expires_days" : (date.fromtimestamp(int(c["expires"])) - date.today()).days if int(c["expires"]) < 200000000000 else -1
        } for c in cookies]

        await browser_context.close()

        output["status"] = "success"
        output["status_msg"] = "Successfully extracted data"

        return output

    except Exception as e:
        error_msg = f"Error extracting data from {url}: {e}"
        logging.debug(error_msg)

        output["status"] = "error"
        output["status_msg"] = error_msg

        return output

def crawl_batch(urls, batch_size=10, **kwargs):
    """
    Run the crawler for multiple URLs in batches.
    """
    # not tested yet
    results = []
    for urls_batch in batch(urls, batch_size):
        results.append(asyncio.run(crawl_url(urls_batch, **kwargs)))

        # data = crawl_url(urls_batch, browser, blocklist, screenshot)
        # await store_results([r for r in await asyncio.gather(*data) if r], table_name, conn) # run all urls in parallel
    return results