# auto-consent-checks
Automatically check for GDPR/CCPA consent by running a Playwright headless browser to check for marketing and analytics scripts firing before and after consent. 

## How it works
Playwright allows you to automate browser windows. This script takes a list of URLs, runs a Playwright browser instance and fetches data about cookies and requested domains for each URL. The URLs are fetched asynchronously and in batches to speed up the process. After the URL is fetched, the script tries to identify the consent manager and click 'accept' to determine if and what marketing and analytics tags are fired before and after consent. It uses a 'blacklist' to determine whether a domain is a tracking (marketing/analytics) domain.

## CLI Arguments
| Argument | Description |
|----------|-------------|
| url      | either a single URL starting with 'http' or file containing one url per line
| --batch_size | Number of URLs to open simultaneously, default is 15
| --debug   | Flag to log output for debugging |
| --nd_json | Add flag to store output as new line delimited JSON for use in e.g. BigQuery
|--no_screenshot   | Flag to not save screenshots |