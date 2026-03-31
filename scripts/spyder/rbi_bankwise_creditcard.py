import requests
from bs4 import BeautifulSoup
import pandas as pd
from io import StringIO
import csv

from urllib.parse import urljoin, urlparse
import utils

list_of_pages = [
    {
    "pg_name": "Credit-Cards-by-Foreign-Banks",
    "row_range": [(1, 6), (45, 58)],
    "column_range": [(1, 2), (9, 9)],
    "month_to_fetch": "Jan 2026",
    "term_to_look": "Foreign Banks"
    },
    {
    "pg_name": "Credit-Cards-by-Private-Sector-Banks",
    "row_range": [(1, 6), (23, 43)],
    "column_range": [(1, 2), (9, 9)],
    "month_to_fetch": "Jan 2026",
    "term_to_look": "Private Sector Banks"
    },
    {
    "pg_name": "Credit-Cards-by-Public-Sector-Banks",
    "row_range": [(1, 6), (10, 21)],
    "column_range": [(1, 2), (9, 9)],
    "month_to_fetch": "Jan 2026",
    "term_to_look": "Public Sector Banks"
    },
    {
    "pg_name": "Credit-Cards-by-Small-Finance-Banks",
    "row_range": [(1, 6), (67, 77)],
    "column_range": [(1, 2), (9, 9)],
    "month_to_fetch": "Jan 2026",
    "term_to_look": "Small Finance Banks"
    },
    ]

def parse_website(file_name: str):
    # Step 1: Fetch the list page to find the latest data link
    list_url = "https://rbi.org.in/scripts/ATMView.aspx"
    
    # Crucial: Real browsers send these headers. Without them, RBI will drop the connection.
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    session = requests.Session()
    response = session.get(list_url, headers=headers, timeout=20, verify=True)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    first_table = soup.find('table')

    if first_table:
        rows = first_table.find_all('tr')
        cells = rows[3].find_all('td')
        date = cells[0].text.strip()
        link_tag = cells[1].find('a')
        if link_tag and link_tag.has_attr('href'):
            href = link_tag['href']
            print(f"Date: {date}, href: {href}")
        
            # Download the file from href and save it locally
            download_url = urljoin(list_url, href)
            download_response = requests.get(download_url)
            if download_response.status_code == 200:
                file_name = file_name + ".xlsx"
                with open(file_name, "wb") as f:
                    f.write(download_response.content)
                print(f"File downloaded and saved as {file_name}")
            else:
                print(f"Failed to download file, status code: {download_response.status_code}")

def usecase_customization(data_list_of_lists):
    return data_list_of_lists
    # Find the index of the row whose first element is "Value"
    updated = []
    for row in data_list_of_lists:
        if row and isinstance(row, list) and row[0] == "Value (Crore)":
            # Multiply each cell (except the first column) by 100, if it's a number
            new_row = ["Value (Crore)"]
            for cell in row[1:]:
                try:
                    new_val = float(cell)*100
                    # Convert back to string to preserve original structure (if needed)
                    # new_row.append(str(new_val))
                    # If you want numeric output, use float
                    new_row.append(new_val)
                except Exception:
                    # If cell is not convertible to float, just append as is
                    new_row.append(cell)
            updated.append(new_row)
        else:
            updated.append(row)
    # return updated



def main():   
    failed_cases = []
    file_name = "rbi_bankwise_file_J"
    # parse_website(file_name);
    

    for each in list_of_pages:
        print(f"==================== START ==========================")
        pg_name = each["pg_name"]
        row_ranges = each["row_range"]
        column_ranges = each["column_range"]
        month_to_fetch = each["month_to_fetch"]
        term_to_look = each["term_to_look"]

        try:
            excel_to_gemini_context = utils.excel_to_gemini_context(
                file_name + ".xlsx",
                f"Extract the Data for {month_to_fetch} for Number of {term_to_look}.",
                sheet_name="Sheet1",
                row_ranges=row_ranges,
                column_ranges=column_ranges
            )
            print(f"==================================================")
            print(f"excel_to_gemini_context: {excel_to_gemini_context}")

            raw_response = utils.send_prompt_to_gemini(excel_to_gemini_context)
            print(f"Gemmin response for data to append:  {raw_response}")
            # Check if raw_response is an empty list (either [], '[]', or equivalent when stringified)
            if not raw_response or (isinstance(raw_response, str) and raw_response.strip() in ["[]", ""]):
                raise Exception("Gemini response was empty. Skipping this iteration.")

            data_array_to_append = utils.clean_list_from_gemini_response(
                raw_response, customization_fn=usecase_customization
            )
            print(f"==================================================")
            print("Check Data to Append: ", data_array_to_append)

            raw_report_url = utils.fetch_csv_from_db(pg_name)
            csv_path = raw_report_url.replace(
                'https://assets.marketreports.in/Data',
                'https://storage.googleapis.com/marketreports/Data'
            )

            original_csv_array = utils.convert_csv_to_arrayOfarray(csv_path)
            print(f"==================================================")
            print("original_csv_array: ", original_csv_array)

            prompt_to_format_new_array = f"""
                ### MISSION
                You are a High-Precision Data Reformatter. Your task is to process the 'Source Data' and convert it into a standardized 2-column grid format.

                ### INPUT DATA
                1. **Source Data**: {data_array_to_append}

                ### STRICT CONSTRAINTS
                1. **Row Parity**: The output MUST contain exactly {len(data_array_to_append)} rows. Do not merge, skip, or omit any rows from the Source Data.
                2. **2-Column Structure**: Every inner list in your output MUST contain exactly two elements: [Item Name, Value].
                3. **Selection Logic (First and Last)**:
                - For every row, extract the **1st element** (the Label/Item Name).
                - Extract the **very LAST element** in that same row (the latest value/date).
                - DISCARD all intermediate elements (everything between the first and the last).
                4. **Zero-Value Enforcement**: If the last element is empty, null, or 0, you MUST still output it as "0". 
                5. **Data Integrity**: Retain original formatting (Septimals, Indian numbering commas like 89,26,358). Do not summarize or calculate anything.

                ### EXECUTION STEPS
                1. Scan the Source Data row by row.
                2. Verify that the total number of rows is exactly {len(data_array_to_append)}.

                ### OUTPUT FORMAT
                1. Return ONLY the reformatted array of arrays. No conversational text.
                2. Example of the expected shape: 
                [['Item A', 'Value X'], ['Item B', 'Value Y']]
            """

            data_array_to_append_formatted = utils.send_prompt_to_gemini(prompt_to_format_new_array)
            print("Check123: ", data_array_to_append_formatted)
            clean_data_to_append_formatted  = utils.clean_list_from_gemini_response(data_array_to_append_formatted)
            print(f"==================================================")
            last_month_table_formatted = utils.format_date_inHeader(clean_data_to_append_formatted)
            print("last_month_table_formatted: ", last_month_table_formatted)

            # Check if the header (first row) has the 'month_to_fetch' as the last element, formatted as the last date of the month (e.g., '31-12-2025' for 'Sept 2025')
            from scripts.spyder.utils import get_last_date_of_month  # Ensure import at top-level as needed
            formatted_month = get_last_date_of_month(month_to_fetch)
            print("formatted_month: ", formatted_month)
            if last_month_table_formatted and (last_month_table_formatted[0][-1] == formatted_month):
                if len(last_month_table_formatted[0])==2:
                    print("Manual Appending")
                    updated_table_manual = utils.update_arrayOfarray(original_csv_array, last_month_table_formatted)
                    # print(f"==================================================")
                    # print("updated_table_manual: ", updated_table_manual)
                    updated_table = updated_table_manual
                    print(f"==================================================")
                    print("updated_table: ", updated_table)
                    gs_object = utils.update_bucket_obj(updated_table, csv_path)
                    print("gsObject: ", gs_object)

            print(f"====================== END =========================")
        except Exception as e:
            failed_cases.append({"pg_name": pg_name, "month_to_fetch": month_to_fetch, "error": str(e)})
            print(f"[FAILED] {pg_name} ({month_to_fetch}): {e}")

    if failed_cases:
        print("\n=== FAILED CASES ===")
        for fc in failed_cases:
            print(f"  - {fc['pg_name']} ({fc['month_to_fetch']}): {fc['error']}")



if __name__ == "__main__":
    main()
