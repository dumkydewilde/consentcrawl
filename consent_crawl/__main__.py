import asyncio, os, re, base64, json, sqlite3
import logging, argparse, requests
from playwright.async_api import async_playwright
from datetime import date
from tqdm import tqdm
from crawl import click_consent_manager, crawl_url, get_extract_schema
from utils import batch
from blocklists import Blocklists
from pathlib import Path



async def store_results(data, table_name=None, file=None, db_file=None):
    if file is not None:
        Path.mkdir(Path(db_file).parent, exist_ok=True)
        with open(file, 'a') as f:
            f.writelines([json.dumps(item) + "\n" for item in data])
    
    if db_file is not None:
        Path.mkdir(Path(db_file).parent, exist_ok=True)
        
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({','.join([f'{k} TEXT' for k in get_extract_schema().keys()])})")
        conn.commit()
        
        logging.info(f"Storing {len(data)} records in database")
        c = conn.cursor()
        for d in data:
            logging.info(f"Storing {d['url']}")
            d = {k: json.dumps(v) if type(v) in [dict, list, tuple] else v for k, v in d.items()}
            c.execute(f"INSERT INTO {table_name} VALUES ({','.join(['?' for k in d.keys()])})", tuple(d.values()))
            conn.commit()
        
        conn.close()



async def process_urls(urls, batch_size, ad_domains, headless=True, screenshot=True):
    """
    Start the Playwright browser, run the URLs to test in batches asynchronously
    and write the data to a file.
    """
    table_name = "crawl_results"
    db_file = "crawl_results.db"

    async with async_playwright() as p:
        logging.debug("Starting browser")
        browser = await p.chromium.launch(headless=headless)

        for urls_batch in tqdm(batch(urls, batch_size), total=round(len(urls)/batch_size)):
            data = [crawl_url(url, browser, ad_domains, screenshot) for url in urls_batch]
            await store_results([r for r in await asyncio.gather(*data) if r], table_name=table_name, db_file=db_file) # run all urls in parallel
            logging.debug(f"Retrieved batch of {len(data)} URLs")
    
        await browser.close()

    
    


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument('url', help='URL or file with URLs to test')
    parser.add_argument('--debug', default=False, action="store_true")
    parser.add_argument('--headless', default=True, type=bool)
    parser.add_argument('--screenshot', default=False, action="store_true")
    parser.add_argument('--batch_size', default=15, type=int)

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)

    if not os.path.isdir('screenshots') and args.screenshot:
        os.mkdir('screenshots')

    # List of URLs to test
    if args.url.endswith(".txt"):
        with open(args.url, 'r') as f:
            urls = list(set([l.strip().lower() for l in set(f.readlines()) if len(l) > 0 and not l.startswith("#")]))
        
        urls = [url for url in urls]
    elif args.url != "":
        urls = args.url.split(",")
    else:
        logging.error("No URL or valid .txt file with URLs to test")

    # Bootstrap blocklists
    blocklists = Blocklists()
    ad_domains = blocklists.get_domains()
    
    asyncio.run(process_urls(urls, args.batch_size, ad_domains, args.headless, args.screenshot))
    