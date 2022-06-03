import asyncio
import os
import re
from playwright.async_api import async_playwright
import base64
import json
from datetime import date
import requests
import logging
import argparse

def batch(iterable, n=1):
    """
    Turn any iterable into a generator of batches of batch size n
    """
    l = len(iterable)
    for ndx in range(0, l, n):
        yield iterable[ndx:min(ndx + n, l)]

async def extract_data(url, browser, blacklist=[], consent_accept_selectors={}, screenshot=True):
    """
    Open a new browser context with a URL and extract data about cookies and 
    tracking domains before and after consent.
    Returns:
    - All third party domains requested
    - Third party domains requested before consent
    - All tracking domains (based on blacklist)
    - Tracking domains before consent
    - All cookies (name, domain, expiration in days)
    - Cookies set before consent
    - Consent manager that was used on the site
    - Screenshot of the site before consenting
    """
    try:
        if not url.startswith("http"):
            url = "http://" + url

        browser_context = await browser.new_context()
        domain_name = re.search("(?:https?://)?(?:www.)?([^/]+)", url).group(0)
        base64_url = base64.urlsafe_b64encode(domain_name.encode('ascii')).decode('ascii')
        
        req_urls = []
        
        page = await browser_context.new_page()
        page.on("request", lambda req: req_urls.append(req.url))
        
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(5000) # additional wait time just to be sure as consent managers can sometimes take a while to load

        screenshot_file = ""
        if screenshot:
            await page.screenshot(path=f'./screenshots/screenshot_{base64_url}.png')
            screenshot_file = f'./screenshots/screenshot_{base64_url}.png'

        # Capture data pre-consent
        thirdparty_requests = list(filter(lambda req_url: not domain_name in req_url, req_urls))
        third_party_domains_no_consent = list(set(map(lambda r: re.search("https?://(?:www.)?([^\/]+\.[^\/]+)", r).group(1), thirdparty_requests)))
        tracking_domains_no_consent = list(set([re.search("[^\.]+\.[a-z]+$", d).group(0) for d in third_party_domains_no_consent if d in blacklist]))

        cookies = await browser_context.cookies()
        cookies_no_consent = [{
            "name" : c["name"],
            "domain" : c["domain"],
            "expires_days" : (date.fromtimestamp(int(c["expires"])) - date.today()).days if int(c["expires"]) < 200000000000 else -1 # prevent out of range errors for the date
        } for c in cookies]

        
        
        # try to accept full marketing consent
        consent_manager = "none detected"
        for k in consent_accept_selectors.keys():
            if await page.locator(consent_accept_selectors[k]).count() > 0:
                consent_manager = k
                try:
                    # explicit wait for navigation as some pages will reload after accepting cookies
                    async with page.expect_navigation(wait_until="networkidle", timeout=15000):
                        await page.click(consent_accept_selectors[k], delay=10)
                except Exception as e:
                    logging.debug(url, e)
                break

        thirdparty_requests = list(filter(lambda req_url: not domain_name in req_url, req_urls))
        third_party_domains_all = list(set(map(lambda r: re.search("https?://(?:www.)?([^\/]+\.[^\/]+)", r).group(1), thirdparty_requests)))
        tracking_domains_all = list(set([re.search("[^\.]+\.[a-z]+$", d).group(0) for d in third_party_domains_all if d in blacklist]))

        cookies = await browser_context.cookies()
        
        cookies_all = [{
            "name" : c["name"],
            "domain" : c["domain"],
            "expires_days" : (date.fromtimestamp(int(c["expires"])) - date.today()).days if int(c["expires"]) < 200000000000 else -1
        } for c in cookies]

        await browser_context.close()

        return {
            "id" : base64_url,
            "url" : url,
            "cookies_all" : cookies_all,
            "cookies_no_consent" : cookies_no_consent,
            "third_party_domains_all" : third_party_domains_all,
            "third_party_domains_no_consent" : third_party_domains_no_consent,
            "tracking_domains_all" : tracking_domains_all,
            "tracking_domains_no_consent" : tracking_domains_no_consent,
            "consent_manager" : consent_manager,
            "screenshot" : screenshot_file
        }
    except Exception as e:
        logging.debug(url, e)
        return None

async def process_urls(urls, batch_size, blacklist, consent_accept_selectors, headless=True, screenshot=True, ndjson=False):
    """
    Start the Playwright browser, run the URLs to test in batches asynchronously
    and write the data to a file.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)

        results = []
        for urls_batch in batch(urls, batch_size):
            data = [extract_data(url, browser, blacklist, consent_accept_selectors, screenshot) for url in urls_batch]
            results.extend([r for r in await asyncio.gather(*data) if r]) # run all urls in parallel
    
        await browser.close()

        with open('site_data.json', 'w') as f:
            if ndjson:
                f.writelines([json.dumps(r) + "\n" for r in results])
            else:
                f.write(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument('url')
    parser.add_argument('--debug', default=False, action="store_true")
    parser.add_argument('--ndjson', default=False, action="store_true")
    parser.add_argument('--headless', default=False, action="store_true")
    parser.add_argument('--no_screenshot', default=False, action="store_true")
    parser.add_argument('--batch_size', default=15, type=int)

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    if not os.path.isdir('screenshots') and not args.no_screenshot:
        os.mkdir('screenshots')


    # List of URLs to test
    urls = []
    if not args.url.startswith("http"):
        # assume it's a file if it doesn't start with http
        with open(args.url, 'r') as f:
            urls = [l.strip().lower() for l in set(f.readlines()) if len(l) > 0]
    else:
        urls = [args.url]

    # Retrieve analytics and marketing domains from blacklist (https://github.com/anudeepND/blacklist)
    ad_domains_file = requests.get("https://hosts.anudeep.me/mirror/adservers.txt").text
    ad_domains = set([line.split('0.0.0.0 ')[-1] 
        for line in ad_domains_file.split("\n") 
        if not line.startswith('#')])

    # CSS selectors for accepting consent
    consent_accept_selectors = {}
    with open('consent_managers.json', 'r') as f:
        consent_accept_selectors = json.load(f)

    asyncio.run(process_urls(urls, args.batch_size, ad_domains, consent_accept_selectors, args.headless, not args.no_screenshot, args.ndjson))
    