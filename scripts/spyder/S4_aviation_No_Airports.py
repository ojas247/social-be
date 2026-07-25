from utils import extract_text_from_pdf, get_gemini_api_key, fetch_item_names_from_TimeSeriesData, scrape_js_page, get_gemini_api_key, parse_json_from_llm_output
import requests
import pandas as pd
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
        "Using the input data, count the no of entries (ie the no of airports) in each categories."
        "Return the values in a json format where key is the item name and value is the Count of cities in that Category. Please note that the values should be pure digits, WITHOUT any UNITS or other text. Also, if the value is not found, return 0\n\n"
        "The json format should be like this: {{'item_name': 'value', 'item_name2': 'value2', ...}}\n\n"
        f"Here is the Input data:\n{input_text}"
    )
    response = model.generate_content(prompt)
    return response.text


def main():
    date = "May 2026"
    kind = "TimeSeriesData"
    property_name = "dataName"
    dataName = "Number of Airports by Categories in India"
    month = "May"
    pdf_path = f"https://www.aai.aero/sites/default/files/traffic-news/{month}2k26Annex2.pdf"
    
    
    item_names = fetch_item_names_from_TimeSeriesData(kind, property_name, dataName)
    input_text = extract_text_from_pdf(pdf_path)
    api_key = get_gemini_api_key()
    gemini_output = extract_matching_data_with_gemini(
        api_key, input_text, item_names
    )
    print("gemini_output: ", gemini_output)
    parsed = parse_json_from_llm_output(gemini_output)
    print("parsed: ", parsed)
    update_Datastore(parsed, date, dataName, "StagingData_v1", "Monthly", "S4", pdf_path)

if __name__ == "__main__":
    main()
