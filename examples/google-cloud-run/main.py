import os, logging, asyncio
from fastapi import FastAPI
from consentcrawl import crawl, blocklists
from playwright.async_api import async_playwright

app = FastAPI()
browser = None
blockers = None


# On startup fetch the blocklists and start the browser
@app.on_event("startup")
async def startup_event():
    global browser
    global blockers

    # Blocklists
    blockers = blocklists.Blocklists()
    logging.info(f"Loaded {len(blockers.get_domains())} domains from blocklists")

    # Browser
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True, channel="msedge")


@app.on_event("shutdown")
async def shutdown_event():
    await browser.close()


@app.post("/consentcrawl")
async def consentcrawl(url: str):
    try:
        # You can optionally add other tasks or multiple URLs to crawl and run them in parallel
        crawl_task = crawl.crawl_url(
            url,
            browser=browser,
            screenshot=bool(os.environ.get("CC_SCREENSHOTS", False)),
            tracking_domains_list=blockers.get_domains(),
        )
        result = await asyncio.gather(crawl_task)

        if result.get("status", "error") == "error":
            logging.error(result.get("status_msg", "Unknown error"))
            if "net::ERR_NAME_NOT_RESOLVED" in result.get("status_msg", ""):
                result = {"error": "net::ERR_NAME_NOT_RESOLVED", **result}
            else:
                result = {"error": result.get("status_msg", "Unknown error"), **result}

        return {"results": [result]}

    except Exception as e:
        logging.error(e)
        return {
            "error": "Server error when handling your request. Please try again later."
        }
