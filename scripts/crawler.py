import datetime
import hashlib
import os
from datetime import date
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import time
from bs4 import BeautifulSoup
from boilerpy3 import extractors
from google.cloud import datastore

client = datastore.Client(project="marketreports")
 

def main():
    # Skip if last successful run was within the last 7 days
    script_dir = os.path.dirname(os.path.abspath(__file__))
    flag_file = os.path.join(script_dir, 'crawler_run_flag.txt')
    today = date.today()

    if os.path.exists(flag_file):
        with open(flag_file, 'r') as f:
            last_run = f.read().strip()
        try:
            last_run_date = date.fromisoformat(last_run)
            days_since = (today - last_run_date).days
            if days_since < 700: # ######3
                print(
                    f"Last run was {days_since} day(s) ago ({last_run}). "
                    "Skipping until 7 days have passed."
                )
                exit(0)
        except ValueError:
            print(f"Invalid date in flag file ({last_run!r}); re-running crawl.")

    # Mark today as run
    with open(flag_file, 'w') as f:
        f.write(str(today))
    # =======================================
    # YOUR ACTUAL SCRIPT CODE STARTS HERE
    # =======================================
    crawl_pages()

def crawl_pages():
    query = client.query(kind='Crawler_Dashboard')
    query.keys_only() 
    results = list(query.fetch())
    for entity in results:
        key = entity.key
        key_name = key.id_or_name
        print(f"URL Key Name: {key_name}")
        process_data(key)
        # if str(key_name).lower() == "https://ppac.gov.in/prices/international-prices-of-crude-oil".lower():
        #     process_data(key)
        # else:
        #     print(f"Skipping: {key_name}")


def process_data(page_entity_key):
    update_checksum_obj = {}
    crawler_entity = client.get(page_entity_key)

    source_url = page_entity_key.id_or_name
    attribute = crawler_entity.get('sectionAttribute')
    author = crawler_entity.get('author')
    pg_checksum_new = None
    if attribute is not None:
        pg_checksum_new = fetch_checksum_of_section(source_url, attribute) # approach 1
        print(f"Process Completed: {source_url} | APPROACH 1")
        if pg_checksum_new is None:
            pg_checksum_new = fetch_checksum_of_section_using_selenium(source_url, attribute) # approach 2
            print(f"Process Completed: {source_url} | APPROACH 2")
    if pg_checksum_new is None:
        pg_checksum_new = fetch_checksum_using_selenium(source_url) # approach 3 
        print(f"Process Completed: {source_url} | APPROACH 3")
    if pg_checksum_new is None:
        pg_checksum_new = fetch_checksum_of_pg(source_url) # approach 4
        print(f"Process Completed: {source_url} | APPROACH 4")

    try:
        checksum_old = crawler_entity.get('cksumOfPgThatIsPublished', '')
        updated_on_old = crawler_entity.get('updatedOn', '')
        pg_is_same = str(pg_checksum_new).lower() == str(checksum_old).lower()
        print(f"Old: {checksum_old}")
        print(f"New: {pg_checksum_new}")
        print(f"pgIsSame: {pg_is_same}")

        update_checksum_obj['SourceURL'] = source_url
        update_checksum_obj['checkSum_new'] = pg_checksum_new
        update_checksum_obj['checksum_old'] = checksum_old
        update_checksum_obj['changeInChecksum'] = not pg_is_same
        update_checksum_obj['updatedOn_old'] = updated_on_old
        update_checksum_obj['key'] = source_url
        if not pg_is_same:
            print("Pg Changed")
            update_checksum_obj['cacheUpdateDate'] = datetime.datetime.now()
        update_checksum(crawler_entity, update_checksum_obj)

    except Exception as e:
        print(f"Error Caught: {e}")

def update_checksum(page_entity, update_checksum_obj):
    page_entity['checksum'] = update_checksum_obj['checkSum_new']
    page_entity['pgChanged'] = update_checksum_obj['changeInChecksum']
    page_entity['lastCrawled'] = datetime.datetime.now()

    if update_checksum_obj['changeInChecksum']:
        page_entity['cacheUpdateDate'] = datetime.datetime.now()

    client.put(page_entity)

def fetch_checksum_of_pg(source_url):
    try:
        print("Fetching Checksum of Entire Page | APPROACH 4")
        extractor = extractors.DefaultExtractor()
        text = extractor.get_content(source_url)
        text = ' '.join(text.split())
        hash_object = hashlib.md5(text.encode('utf-8'))
        return hash_object.hexdigest()
    except Exception as e:
        print(f"Error Caught: {e}")
        return None

def fetch_checksum_of_section(url, section_attribute):
    try:
        print("Fetching Checksum of SectionAttribute | APPROACH 1")
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        section = soup.select_one(section_attribute)

        relevant_text = None
        if section is not None:
            relevant_text = section.get_text(separator=' ', strip=True)
            print(f"Extracted content1: {relevant_text}")
        else:
            print(f"⚠️ No element matched the CSS selector. Element: {section_attribute}")
            return None

        hash_object = hashlib.md5(relevant_text.encode('utf-8'))
        return hash_object.hexdigest()

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None

def fetch_checksum_of_section_using_selenium(source_url, css_selector):
    driver = None
    try:
        print(f"Fetching Checksum of selector using Selenium: {css_selector} | APPROACH 2")
        
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        
        driver = webdriver.Chrome(options=chrome_options)
        driver.get(source_url)
        
        # Explicitly wait up to 10 seconds for the element to appear
        wait = WebDriverWait(driver, 5)
        element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, css_selector)))
        
        # Get content (text or innerHTML depending on your needs)
        # Using text captures what the user sees; using attribute('innerHTML') captures structural changes
        content = element.text
        
        # Normalize text: removes extra whitespace/newlines that break hashes
        normalized_content = ' '.join(content.split()).strip()
        
        if not normalized_content:
            print("Warning: Selector found but content is empty.")
            return None
        
        # Generate MD5 hash
        hash_hex = hashlib.md5(normalized_content.encode('utf-8')).hexdigest()
        
        return hash_hex
        
    except Exception as e:
        print(f"Error in fetch_selector_checksum: {e}")
        return None
    finally:
        if driver:
            driver.quit()

def fetch_checksum_using_selenium(source_url):
    try:
        print("Fetching Checksum of Page using selenium | APPROACH 3")
        # Set up Chrome options for headless mode (optional, can remove --headless for visible browser)
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        
        driver = webdriver.Chrome(options=chrome_options)
        driver.get(source_url)
        
        # Wait for page to load (adjust time as needed)
        time.sleep(3)
        
        # Extract text from the body
        body_text = driver.find_element(By.TAG_NAME, "body").text
        
        # Normalize text
        text = ' '.join(body_text.split()).strip()
        
        # Generate MD5 hash
        hash_object = hashlib.md5(text.encode('utf-8'))
        hash_hex = hash_object.hexdigest()
        
        driver.quit()
        return hash_hex
        
    except Exception as e:
        print(f"Error in fetch_checksum_using_selenium: {e}")
        if 'driver' in locals():
            driver.quit()
        return None

if __name__ == "__main__":
    main()



