from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import os
import re
from pathlib import Path


TARGET_REPORT = "Diffusion Indices based on CPI data"
MAIN_URL = (
    "https://data.rbi.org.in/#/dbie/reports/Statistics/"
    "Real%20Sector/Prices%20&%20Wages"
)


def _find_report_anchor(driver, target_text: str):
    """
    Angular renders each report name as letter-separated text nodes with
    empty <span class="highlight"> between them, so XPath contains(text(), ...)
    never matches. Use Selenium's aggregated .text on <a> / <td> instead.
    """
    for anchor in driver.find_elements(By.CSS_SELECTOR, "table td.tdCenter a"):
        if target_text.lower() in (anchor.text or "").replace("\n", " ").lower():
            return anchor

    for row in driver.find_elements(By.CSS_SELECTOR, "table tr"):
        if target_text.lower() in (row.text or "").replace("\n", " ").lower():
            anchors = row.find_elements(By.CSS_SELECTOR, "td.tdCenter a, a")
            if anchors:
                return anchors[0]
            cells = row.find_elements(By.CSS_SELECTOR, "td")
            if cells:
                return cells[0]
    return None


def _element_page_point(driver, element) -> dict:
    """Viewport point in the top-level page (accounts for iframe offsets)."""
    return driver.execute_script(
        """
        const el = arguments[0];
        const r = el.getBoundingClientRect();
        let x = r.left + r.width / 2;
        let y = r.top + r.height / 2;
        let win = window;
        while (win !== win.top) {
          const frameEl = win.frameElement;
          if (!frameEl) break;
          const fr = frameEl.getBoundingClientRect();
          x += fr.left;
          y += fr.top;
          win = win.parent;
        }
        return {x, y, width: r.width, height: r.height};
        """,
        element,
    )


def _cdp_click(driver, element) -> None:
    """Dispatch a real mouse press/release via CDP at page-absolute coords."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
        element,
    )
    time.sleep(0.2)
    point = _element_page_point(driver, element)
    print(f"CDP click at ({point['x']:.1f}, {point['y']:.1f})")
    # CDP events are always relative to the top-level viewport.
    driver.switch_to.default_content()
    for event_type, buttons in (
        ("mouseMoved", 0),
        ("mousePressed", 1),
        ("mouseReleased", 0),
    ):
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {
                "type": event_type,
                "x": point["x"],
                "y": point["y"],
                "button": "left" if event_type != "mouseMoved" else "none",
                "buttons": buttons,
                "clickCount": 1 if event_type != "mouseMoved" else 0,
            },
        )


def _normalize_opendoc_url(url: str) -> str:
    # Angular sometimes emits "https://data.rbi.org.in:/BOE/..."
    return re.sub(r"^(https?://[^/:]+):/", r"\1/", url)


def _install_window_open_hook(driver) -> None:
    driver.execute_script(
        """
        window.__openedUrls = [];
        const orig = window.open;
        window.open = function(url, name, specs) {
          if (url) window.__openedUrls.push(String(url));
          return orig.apply(this, arguments);
        };
        """
    )


def _wait_for_opendoc_url(driver, timeout: float = 15) -> str | None:
    end = time.time() + timeout
    while time.time() < end:
        urls = driver.execute_script("return window.__openedUrls || [];") or []
        for url in urls:
            if "OpenDocument" in url or "openDocument" in url:
                return _normalize_opendoc_url(url)
        time.sleep(0.25)
    return None


def _open_report_document(driver, report_elem) -> None:
    """
    Click the Angular report link, then open the tokenized OpenDocument URL.
    """
    _install_window_open_hook(driver)
    before_handles = set(driver.window_handles)

    _cdp_click(driver, report_elem)

    opendoc_url = _wait_for_opendoc_url(driver, timeout=15)
    if opendoc_url:
        print(f"OpenDocument URL: {opendoc_url[:120]}...")
        time.sleep(1)
        new_handles = [h for h in driver.window_handles if h not in before_handles]
        if new_handles:
            driver.switch_to.window(new_handles[-1])
        else:
            driver.switch_to.new_window("tab")
        driver.get(opendoc_url)
        return

    time.sleep(2)
    new_handles = [h for h in driver.window_handles if h not in before_handles]
    if new_handles:
        driver.switch_to.window(new_handles[-1])
        return

    if "/OpenDocument/" not in driver.current_url:
        raise RuntimeError(
            "Report click did not open an OpenDocument URL "
            "(often shows UI5logon when the Angular token open is missed)."
        )


def _switch_to_webi_frame(driver) -> bool:
    """Switch into the iframe that hosts the WebI toolbar / Export button."""
    driver.switch_to.default_content()
    for frame in driver.find_elements(By.TAG_NAME, "iframe"):
        driver.switch_to.default_content()
        try:
            driver.switch_to.frame(frame)
        except Exception:
            continue
        if driver.find_elements(By.CSS_SELECTOR, "#__button60, button[title*='Export']"):
            return True
    driver.switch_to.default_content()
    return False



def _wait_for_export_button(driver, timeout: float = 90):
    end = time.time() + timeout
    while time.time() < end:
        if _switch_to_webi_frame(driver):
            buttons = driver.find_elements(By.CSS_SELECTOR, "#__button60")
            if buttons and buttons[0].is_displayed() and buttons[0].is_enabled():
                return buttons[0]
        time.sleep(1)
    raise TimeoutError("Export button (#__button60) not found in WebI iframe")


def _ui5_fire_press(driver, control_id: str) -> str:
    return driver.execute_script(
        """
        const id = arguments[0];
        if (!(window.sap && sap.ui && sap.ui.getCore)) return 'no-sap';
        const control = sap.ui.getCore().byId(id);
        if (!control) return 'no-control';
        if (control.firePress) { control.firePress(); return 'firePress'; }
        const el = document.getElementById(id);
        if (el) { el.click(); return 'dom-click'; }
        return 'failed';
        """,
        control_id,
    )


def _export_excel_from_toolbar(driver) -> None:
    """
    Click File > Export (Ctrl+E) via SAP UI5, keep Excel selected, confirm.
    The toolbar lives inside an iframe; Selenium ActionChains often miss it.
    """
    export_btn = _wait_for_export_button(driver)
    print(
        f"Found Export button id={export_btn.get_attribute('id')} "
        f"title={export_btn.get_attribute('title')!r}"
    )

    result = _ui5_fire_press(driver, "__button60")
    print(f"Triggered Export via UI5: {result}")
    if result not in ("firePress", "dom-click"):
        # Fallback: page-absolute CDP click on the icon/button
        icon = driver.find_elements(By.CSS_SELECTOR, "#__button60-img")
        _cdp_click(driver, icon[0] if icon else export_btn)
        if not _switch_to_webi_frame(driver):
            raise RuntimeError("Lost WebI iframe after Export click fallback")

    # Wait for Export dialog (Excel is usually pre-selected).
    WebDriverWait(driver, 30).until(
        EC.visibility_of_element_located(
            (
                By.CSS_SELECTOR,
                ".sapWingExportDialog, .wingTestExportDialog, "
                "[id*='sapWingExportDialog']",
            )
        )
    )
    print("Export dialog opened")

    # Ensure Excel is selected in the format list.
    excel_items = driver.find_elements(
        By.CSS_SELECTOR,
        "li.wingTestExportMasterListEntry, li[data-customclass*='Excel'], "
        "li.sapWingExportMasterListEntry",
    )
    excel_item = None
    for item in excel_items:
        if "excel" in (item.text or "").strip().lower() and item.is_displayed():
            excel_item = item
            break
    if excel_item is None:
        for item in driver.find_elements(By.XPATH, "//li[.//span[normalize-space()='Excel']]"):
            if item.is_displayed():
                excel_item = item
                break
    if excel_item is not None:
        selected = (excel_item.get_attribute("aria-selected") or "").lower() == "true"
        if not selected:
            item_id = excel_item.get_attribute("id")
            print(f"Selecting Excel list item {item_id}")
            driver.execute_script("arguments[0].click();", excel_item)
        else:
            print("Excel already selected")
    else:
        print("Warning: Excel list item not found; relying on dialog default")

    # Confirm Export button in the dialog footer.
    confirm = None
    for sel in (
        "button.wingTestConfirmExportButton",
        ".sapWingExportDialog button.sapMBtnEmphasized",
        "//button[.//bdi[normalize-space()='Export']]",
    ):
        if sel.startswith("//"):
            found = driver.find_elements(By.XPATH, sel)
        else:
            found = driver.find_elements(By.CSS_SELECTOR, sel)
        for btn in found:
            if btn.is_displayed() and "export" in (btn.text or "").strip().lower():
                confirm = btn
                break
        if confirm:
            break

    if confirm is None:
        raise RuntimeError("Could not find Export confirm button in dialog")

    confirm_id = confirm.get_attribute("id")
    print(f"Confirming export via button {confirm_id}")
    confirm_result = _ui5_fire_press(driver, confirm_id)
    if confirm_result not in ("firePress", "dom-click"):
        driver.execute_script("arguments[0].click();", confirm)
    print("Export confirmed")


def download_diffusion_indices_excel(download_dir="downloads"):
    """
    Automates RBI DBIE site navigation to download the
    'Diffusion Indices based on CPI data' Excel file.
    """
    download_dir = os.path.abspath(download_dir)
    chrome_options = webdriver.ChromeOptions()
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    chrome_options.add_experimental_option("prefs", prefs)
    # Headed browser so you can watch navigation / clicks
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option("detach", True)

    os.makedirs(download_dir, exist_ok=True)
    before = {
        f.name
        for f in Path(download_dir).iterdir()
        if f.is_file() and not f.name.endswith(".crdownload")
    }

    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 60)
    try:
        driver.get(MAIN_URL)

        wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table td.tdCenter a"))
        )
        wait.until(lambda d: _find_report_anchor(d, TARGET_REPORT) is not None)
        time.sleep(2)

        elem = _find_report_anchor(driver, TARGET_REPORT)
        if elem is None:
            raise RuntimeError(f"No element found containing text: {TARGET_REPORT}")

        _open_report_document(driver, elem)

        wait.until(
            lambda d: "UI5logon" not in d.current_url
            or "logonSuccessful=true" in d.current_url
        )
        print(f"Document tab URL: {driver.current_url[:160]}")

        print("Waiting for WebI toolbar / Export button...")
        _export_excel_from_toolbar(driver)

        print(f"Waiting for Excel download in: {download_dir}")
        file_found = None
        for _ in range(90):
            current = {
                f.name
                for f in Path(download_dir).iterdir()
                if f.is_file() and not f.name.endswith(".crdownload")
            }
            new_files = sorted(current - before)
            if new_files:
                file_found = new_files[-1]
                print(f"Downloaded: {file_found}")
                break
            time.sleep(1)

        if not file_found:
            raise RuntimeError("Excel file did not appear in download directory.")
        return str(Path(download_dir) / file_found)
    finally:
        driver.quit()


if __name__ == "__main__":
    download_dir = str(Path(__file__).resolve().parent / "downloads")
    download_diffusion_indices_excel(download_dir=download_dir)
