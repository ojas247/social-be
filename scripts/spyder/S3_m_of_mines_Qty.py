from utils import fetch_item_names_from_TimeSeriesData, scrape_js_page, get_gemini_api_key, parse_json_from_llm_output
import requests
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from app.services.datastore_services import update_Datastore

def extract_matching_data_with_gemini(
    api_key: str,
    input_text: str,
    item_names: list[str],
) -> str:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-pro")
    prompt = (
        "You task is the map the input text to the item names.\n"
        f"The item names are: {item_names}\n"
        "Using the input data, fetch values for the item names. "
        "Return the values in a json format where key is the item name and value is the float value, you got from the input text. Please note that the values should be pure digits, WITHOUT any UNITS or other text. Also, if the value is not found, return 0\n\n"
        "The json format should be like this: {{'item_name': 'value', 'item_name2': 'value2', ...}}\n\n"
        f"Here is the Input data:\n{input_text}"
    )
    response = model.generate_content(prompt)
    return response.text



def main():
    date = "Mar 2026"
    kind = "TimeSeriesData"
    property_name = "dataName"
    dataName = "Mineral Production in India"
    page_url = "https://mines.gov.in/webportal/content/production-2024"
    item_names = fetch_item_names_from_TimeSeriesData(kind, property_name, dataName)

    rendered_html = scrape_js_page(page_url)
    soup = BeautifulSoup(rendered_html, 'html.parser')

    # Now process the table normally, exactly as we did before
    container = soup.find('div', class_='table-responsive')

    if container:
        table = container.find('table')
        if table:
            rows = table.find_all('tr')
            data = []
            for row in rows[2:]: # Skip headers
                cells = row.find_all('td')
                if len(cells) >= 2:
                    data.append({
                        "Mineral": cells[0].get_text(strip=True),
                        "Qty_2025_26": cells[-2].get_text(strip=True),
                        # "Value_2025_26": cells[-1].get_text(strip=True)
                    })
            df = pd.DataFrame(data)
            print("\n--- Scraped Data Successfully (input Text) ---")
            print(df.to_string())
            input_text = df.to_string(index=False)
        else:
            print("Could not find table inside the container.")
    else:
        print("Could not find the target container div.")

   
    api_key = get_gemini_api_key()
    gemini_output = extract_matching_data_with_gemini(
        api_key, input_text, item_names
    )
    print("\n--- Gemini Output ---")
    print("gemini_output: ", gemini_output)

    parsed = parse_json_from_llm_output(gemini_output)
    print("\n--- Parsed JSON ---")
    print("parsed: ", parsed)

    update_Datastore(parsed, date, dataName, "StagingData_v1", "Yearly", "S3", page_url)





   

# If this file is run directly, run main()
if __name__ == "__main__":
    main()