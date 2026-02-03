import sys
import os
import openpyxl
from datetime import datetime
from typing import Tuple, Optional, List
import csv
import requests
from io import StringIO

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from app.services.datastore_services import fetch_entities_by_property

list_of_pages = ['Automobile-Domestic-Sales-2025', 'Automobile-Domestic-Sales-2024']



def read_excel_cells(file_path: str, start_row: int = 160) -> List[List]:
    """
    Read an Excel file and extract dates and values from columns B, C, and D starting from a given row.
    
    Args:
        file_path: Path to the Excel file
        start_row: Starting row number (default: 160)
        
    Returns:
        List of lists (array of arrays) where:
        - First array: Dates (months) from column B
        - Second array: Values from column C (corresponding to dates)
        - Third array: Values from column D (corresponding to dates)
    """
    dates = []
    values_c = []
    values_d = []
    
    try:
        # Load the workbook
        workbook = openpyxl.load_workbook(file_path)
        
        # Get the active sheet (or specify sheet name if needed)
        sheet = workbook.active
        
        # Read from start_row downwards until we hit an empty cell in column B
        row = start_row
        while True:
            # Read cell B (date)
            cell_b = sheet[f'B{row}']
            
            # Stop if cell B is empty
            if cell_b.value is None:
                break
            
            # Extract month from date
            month = None
            if isinstance(cell_b.value, datetime):
                # Extract month from datetime
                month = cell_b.value.strftime('%B %Y')  # e.g., "January 2024"
            elif isinstance(cell_b.value, (int, float)):
                # If it's an Excel date serial number, convert it
                try:
                    date_value = openpyxl.utils.datetime.from_excel(cell_b.value)
                    month = date_value.strftime('%B %Y')
                except:
                    # If conversion fails, use as string
                    month = str(cell_b.value)
            else:
                # If it's already a string, use it as is
                month = str(cell_b.value)
            
            dates.append(month)
            
            # Read cell C
            cell_c = sheet[f'C{row}']
            value_c = cell_c.value if cell_c.value is not None else None
            values_c.append(value_c)
            
            # Read cell D
            cell_d = sheet[f'D{row}']
            value_d = cell_d.value if cell_d.value is not None else None
            values_d.append(value_d)
            
            row += 1
        
        # Close the workbook
        workbook.close()
        
        # Return as array of arrays: [dates, values_c, values_d]
        return [dates, values_c, values_d]
        
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        return [[], [], []]
    except Exception as e:
        print(f"Error reading Excel file: {str(e)}")
        return [[], [], []]

def fetch_csv_from_db():    
    for each in list_of_pages:
        # fetch_entities_by_property returns a LIST of dicts
        print("Started to fetch this page", each)
        entities = fetch_entities_by_property('Published_Data_v1', 'slugURL', each)
       
        
        # Check if the list is not empty before accessing
        if entities:
            print ("StartedV1")
            entity = entities[0] # Get the first matching record
            raw_report_url = entity.get('ReportUrl', '')
            
            if raw_report_url:
                report_url = raw_report_url.replace(
                    'https://assets.marketreports.in/Data', 
                    'https://storage.googleapis.com/marketreports/Data'
                )
                # Use standard dict assignment instead of .put()
                # csv_report_obj[each] = report_url
            else:
                print(f"Warning: No ReportUrl found for {each}")
        else:
            print(f"Warning: No entity found for slug {each}")
            
    print('report_url', report_url)
    return report_url

def convert_csv_to_arrayOfarray(csv_url: str) -> List[List[str]]:
    """
    Download a CSV file from a URL (bucket object) and convert it into an array of arrays (list of lists).
    
    Args:
        csv_url: URL of the CSV file stored in the bucket.
        
    Returns:
        A list of rows, where each row is a list of cell values (as strings).
    """
    table: List[List[str]] = []
    
    try:
        # Download the CSV file from the URL
        print(f"Downloading CSV from URL: {csv_url}")
        response = requests.get(csv_url, timeout=30)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        # Decode the content (handle different encodings)
        content = response.content.decode('utf-8-sig')
        
        # Parse the CSV content using StringIO
        csv_file = StringIO(content)
        reader = csv.reader(csv_file)
        
        for row in reader:
            # Convert all cells to strings and append to table
            table.append([str(cell) for cell in row])
        
        print(f"Successfully converted CSV to array of arrays. Rows: {len(table)}")
        return table
        
    except requests.exceptions.RequestException as e:
        print(f"Error downloading CSV from URL: {str(e)}")
        return []
    except Exception as e:
        print(f"Error parsing CSV: {str(e)}")
        return []

if __name__ == "__main__":
    # Replace with your actual file path
    file_path = "C:/Users/Ojas/Downloads/dec.xlsx"
    
    array_of_arrays = read_excel_cells(file_path)
    dates = array_of_arrays[0]
    values_c = array_of_arrays[1]
    values_d = array_of_arrays[2]
    print(f"array_of_arrays: {array_of_arrays}")
    print(f"Values C: {values_c}")
    print(f"Values D: {values_d}")
    # csv_path  = fetch_csv_from_db()
    # print("path: ", csv_path)
    # read_excel_cells(file_path)
    # arrayOfarray = convert_csv_to_arrayOfarray(csv_path);
    # print("table: ", arrayOfarray)
    # update_arrayOfarray();
    # create_bucket_obj();
    # update_datastore_entity();
    
    # print(f"Month from B160: {month}")
    # print(f"Value from C160: {value_c160}")
    # print(f"Value from D160: {value_d160}")

