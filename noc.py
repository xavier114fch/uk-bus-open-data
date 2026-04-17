import time
import json
import logging
import os
import requests
import xmltodict
import xml.etree.ElementTree as ET

# Logger configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Data and URL configuration
data_dir = 'data/noc'
noc_url = 'https://www.travelinedata.org.uk/noc/api/1.0/nocrecords.xml'

# Session configuration
session = requests.Session()
request_timeout = 30

# Retry logic with exponential backoff for handling rate limits and transient errors
def retry_request(url, max_retries=5, backoff_delay=1):
    for attempt in range(max_retries):
        try:
            resp = session.get(url, timeout=request_timeout)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                logger.warning(f'Rate limited (429). Waiting {backoff_delay}s before retry...')
                time.sleep(backoff_delay)
                backoff_delay *= 2
                continue
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f'Request exception: {exc}. Retrying...')
            time.sleep(backoff_delay)
            backoff_delay *= 2
    raise SystemExit(f'Failed to fetch {url} after {max_retries} attempts.')

# Fetch NOC XML, convert to JSON, and save to file
def get_noc():
    logger.info('Fetching NOC XML from API...')
    resp = retry_request(noc_url)
    parser = ET.XMLParser(encoding='iso-8859-1')
    root = ET.fromstring(resp.content, parser=parser)
    xml_str = ET.tostring(root, encoding='utf-8').decode('utf-8')
    logger.info('Converting XML to JSON...')
    json_obj = xmltodict.parse(xml_str, attr_prefix='')
    json_str = json.dumps(json_obj, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, 'noc.json'), 'w', encoding='utf-8') as f:
        f.write(json_str)

# Main function to execute the NOC data fetching process
def main():
    get_noc()

if __name__ == "__main__":
    main()