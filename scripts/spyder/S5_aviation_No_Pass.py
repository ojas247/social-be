from utils import extract_text_from_pdf, get_gemini_api_key, fetch_item_names_from_TimeSeriesData, scrape_js_page, get_gemini_api_key, parse_json_from_llm_output
import requests
import pandas as pd
import tkinter as tk
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
        "Your task is to map the input text to the provided item names.\n"
        f"The valid item names are: {item_names}\n\n"
        "INSTRUCTIONS:\n"
        "1. Locate the second section of the document under the header: यात्री (संख्या में) PASSENGERS (IN NOS.).\n"
        "2. Within that section, focus exclusively on the DOMESTIC Passengers data for each airport category.\n"
        "3. Extract the passenger numbers for the following categories listed in the text:\n"
        "   - अंतर्राष्ट्रीय एयर्पोर्ास INTERNATIONAL AIRPORTS\n"
        "   - पीपीपी अंतर्राष्ट्रीय एयर्पोर्ास PPP INTERNATIONAL\n"
        "   - संयुक्त उधम एयर्पोर्ास JV INTERNATIONAL AIRPORTS\n"
        "   - र्रज्य सर्करर् / निजी अंतर्राष्ट्रीय ST GOVT/PVT INTERNATIONAL\n"
        "   - सीमर शुल्क एयर्पोर्ास CUSTOM AIRPORTS\n"
        "   - घर्ेलूएयर्पोर्ास DOMESTIC AIRPORTS\n"
        "   - र्रज्य सर्करर् / निजी एयर्पोर्ास ST GOVT/PVT AIRPORTS\n\n"
        "OUTPUT FORMAT:\n"
        "Return the data strictly as a valid JSON object. The keys must match the exact item names provided above, "
        "and the values must be the extracted passenger numbers. Do not include any units, commas, percentages, "
        "or extra text in the values—return pure integers only. If a category value is missing or not found, return 0.\n\n"
        "Example format:\n"
        "{{\n"
        "  \"INTERNATIONAL AIRPORTS\": 6231477,\n"
        "  \"PPP INTERNATIONAL\": 2651253\n"
        "}}\n\n"
        f"Here is the Input data:\n{input_text}"
    )
    response = model.generate_content(prompt)
    return response.text


def prompt_run_inputs() -> dict | None:
    """Show a popup form for the 6 run parameters. Returns None if cancelled."""
    defaults = {
        "date": "May 2026",
        "kind": "TimeSeriesData",
        "property_name": "dataName",
        "dataName": "Cargo handled at Indian Airports",
        "month": "May",
        "pdf_path": "https://www.aai.aero/sites/default/files/traffic-news/May2k26Annex3.pdf",
    }
    fields = list(defaults.keys())
    result = {}

    root = tk.Tk()
    root.title("S5 Aviation – Run Inputs")
    root.resizable(False, False)

    entries = {}
    for i, name in enumerate(fields):
        tk.Label(root, text=name, anchor="w").grid(row=i, column=0, sticky="w", padx=8, pady=4)
        entry = tk.Entry(root, width=70)
        entry.insert(0, defaults[name])
        entry.grid(row=i, column=1, padx=8, pady=4)
        entries[name] = entry

    cancelled = {"value": True}

    def on_ok():
        for name in fields:
            result[name] = entries[name].get().strip()
        cancelled["value"] = False
        root.destroy()

    def on_cancel():
        root.destroy()

    btn_frame = tk.Frame(root)
    btn_frame.grid(row=len(fields), column=0, columnspan=2, pady=10)
    tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=5)

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    root.mainloop()

    if cancelled["value"]:
        return None
    return result


def main():
    # inputs = prompt_run_inputs()
    # if inputs is None:
    #     print("Cancelled.")
    #     return

    date = "May 2026" # date of the new data
    month = "May" # date of the new data
    kind = "TimeSeriesData" # to fetch old items from DB
    staging_kind = "StagingData_v1" # to update the data in DB
    granularity = "Monthly" # granularity of the data
    scriptID = "S5" # scriptID of the data
    property_name = "dataName" # to fetch old items from DB
    dataName = "Passengers handled at Indian Airports" # to fetch old items & Update from DB
    pdf_path = f"https://www.aai.aero/sites/default/files/traffic-news/{month}2k26Annex1.pdf"
    

    item_names = fetch_item_names_from_TimeSeriesData(kind, property_name, dataName)
    input_text = extract_text_from_pdf(pdf_path)
    api_key = get_gemini_api_key()
    gemini_output = extract_matching_data_with_gemini(
        api_key, input_text, item_names
    )
    print("gemini_output: ", gemini_output)
    parsed = parse_json_from_llm_output(gemini_output)
    print("parsed: ", parsed)
    update_Datastore( parsed, date, granularity, scriptID, pdf_path,  dataName, staging_kind)


if __name__ == "__main__":
    main()
