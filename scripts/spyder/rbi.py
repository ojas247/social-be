import requests
from bs4 import BeautifulSoup
import pandas as pd
from io import StringIO
import csv
import re
import ast
from urllib.parse import urljoin, urlparse
import utils

list_of_pages = ['Payment-Fraud-in-Domestic-Market']

def parse_website(file_name: str):
    # Step 1: Fetch the list page to find the latest data link
    list_url = "https://www.rbi.org.in/Scripts/PSIUserView.aspx"
    response = requests.get(list_url)

    soup = BeautifulSoup(response.text, 'html.parser')

    first_table = soup.find('table')

    if first_table:
        rows = first_table.find_all('tr')
        cells = rows[1].find_all('td')
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
    return updated



def main():
    file_name = "rbi_file"
    # parse_website(file_name);
    # data_array_to_append = utils.read_excel_cells(file_name + ".xlsx", 159, 159)
    # print(f"array_of_arrays: {data_array_to_append}")

    excel_to_gemini_context = utils.excel_to_gemini_context(file_name + ".xlsx", "Extract the Data for Dec 2025 for Domestic Payment Frauds", "Sheet1", 119, 160)
    # print(f"excel_to_gemini_context: {excel_to_gemini_context}")

    raw_response = utils.send_prompt_to_gemini(excel_to_gemini_context)
    # print(f"response:  {raw_response}")

    # 1) Extract the first ``` ... ``` fenced block (the part that contains [[...]])
    m = re.search(r"```(?:\w+)?\s*([\s\S]*?)\s*```", raw_response)
    data_text = (m.group(1) if m else raw_response).strip()

    # 2) Convert to a real Python list-of-lists safely
    data_list_of_lists = ast.literal_eval(data_text)
    data_array_to_append = usecase_customization(data_list_of_lists)

    print("Check Data to Append: ", data_array_to_append)



    raw_report_url = utils.fetch_csv_from_db(list_of_pages)
    csv_path = raw_report_url.replace(
        'https://assets.marketreports.in/Data', 
        'https://storage.googleapis.com/marketreports/Data'
    )

    original_csv_array = utils.convert_csv_to_arrayOfarray(csv_path);
    # print("original_csv_array: ", original_csv_array)

    # prompt = f"This is original table we need to  retain its format of dates in the top row and value etc. {original_csv_array}. Append data from this table: {data_array_to_append} at the last of the original table. Be careful to NOT change the item names and Maintain the Units and decimals etc "
    # updated_table = utils.send_prompt_to_gemini(excel_to_gemini_context)
    
    updated_table = utils.update_arrayOfarray(original_csv_array, data_array_to_append)
    print("updated_table: ", updated_table)
    gs_object = utils.update_bucket_obj(updated_table, csv_path)
    print("gsObject: ", gs_object)



if __name__ == "__main__":
    main()
