"""Optional browser checks: pip install playwright; playwright install chromium."""

from pathlib import Path
import re
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from groundwater.dashboard import Dashboard, DashboardServer


def main():
    project = Path(__file__).resolve().parents[1]
    candidate = {"properties": {"monitoring_location_id": "USGS-420318121312201",
        "time_series_id": "d750bc81e1d6455190d36fab703385a1", "unit_of_measure": "ft",
        "time": "2025-12-31"}, "geometry": {"coordinates": [-121.52386, 42.05483]}}
    with tempfile.TemporaryDirectory() as directory:
        app = Dashboard(project, Path(directory))
        with DashboardServer(("127.0.0.1", 0), app) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{server.server_port}")
                    page.get_by_role("heading", name="A clearer view below the surface.").wait_for()
                    page.get_by_role("link", name="Explore an example").click()
                    page.get_by_role("heading", name="Best model: Last observed level").wait_for()
                    page.get_by_role("link", name="Analyze a well", exact=True).click()
                    page.get_by_role("button", name="Check data readiness").click()
                    page.get_by_role("heading", name="Your data is ready.").wait_for()
                    page.get_by_role("button", name="Run analysis").click()
                    page.get_by_role("heading", name=re.compile("Best model:")).wait_for(timeout=30000)
                    page.reload()
                    page.get_by_role("heading", name=re.compile("Best model:")).wait_for()
                    page.get_by_role("link", name="Saved results", exact=True).click()
                    page.get_by_role("heading", name="Pick up where you left off.").wait_for()
                    page.locator("#result-filter").fill("snapshot replay")
                    assert page.locator("#saved-table tbody tr").count() == 1
                    page.get_by_role("link", name="Analyze a well", exact=True).click()
                    page.locator("#end").fill("2023-03-01")
                    page.get_by_role("button", name="Check data readiness").click()
                    page.get_by_role("heading", name="Let’s adjust the observations.").wait_for()
                    assert page.get_by_role("button", name="Run analysis").count() == 0
                    page.get_by_role("button", name="Change well or dates").click()
                    page.locator("#source").select_option("live")
                    page.locator("#place").fill("Beaverton, Oregon")
                    page.get_by_role("button", name="Set area", exact=True).click()
                    assert page.locator("#lat").input_value() == "45.49"
                    page.locator("#place").fill("Klamath Falls, Oregon")
                    page.get_by_role("button", name="Set area", exact=True).click()
                    with patch("groundwater.dashboard.USGSClient.discover", side_effect=[
                            RuntimeError("USGS HTTP 429; retries exhausted"), ([candidate], [])]):
                        page.get_by_role("button", name="Find nearby daily wells").click()
                        page.locator("#discovery-status").get_by_text(
                            re.compile("USGS could not finish")).wait_for()
                        page.get_by_role("button", name="Find nearby daily wells").click()
                        page.locator("#well-list button").wait_for()
                        page.locator("#well-map [data-well]").click()
                    assert page.locator("#site").input_value() == "USGS-420318121312201"
                    assert page.locator("#series").input_value() == candidate["properties"]["time_series_id"]
                    page.get_by_role("button", name="Save this well").click()
                    page.locator("#saved-well option").nth(1).wait_for(state="attached")
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    assert not errors, errors
                    browser.close()
                    print("Browser workflow passed: offline analysis, saved results, blocked data, "
                          "mocked discovery retry, map selection, saved well, mobile; no JavaScript errors.")
            finally:
                server.shutdown()
                thread.join()
                app.close()


if __name__ == "__main__":
    main()
