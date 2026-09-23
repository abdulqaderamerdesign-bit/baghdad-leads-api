#!/usr/bin/env python3
"""
Production-Ready Flask Application — Google Maps Scraper
Free, open-source scraping for business data in Baghdad, Iraq.
No paid third-party APIs required (uses Selenium + Chrome Headless).

Usage:
    pip install -r requirements.txt
    python app.py

Deployment (Render / Railway):
    - Install Chrome + ChromeDriver in build environment.
    - Use gunicorn: gunicorn -w 2 -b 0.0.0.0:5000 app:app
"""

import os
import time
import re
import logging
import requests
from typing import List, Dict, Optional

from flask import Flask, request, jsonify
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from selenium.webdriver.common.keys import Keys

# ------------------------------------------------------------------
# App Setup
# ------------------------------------------------------------------
app = Flask(__name__)
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
DEFAULT_LIMIT = 52
MAX_LIMIT = 52
LOCATION = "Baghdad, Iraq"
SEARCH_DELAY = 2.5

WHATSAPP_API_URL = os.environ.get("WHATSAPP_API_URL", "")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")

CATEGORIES = {
    "عيادات طبية": "Medical Clinics",
    "صيدليات": "Pharmacies",
    "مراكز تجميل نسائية": "Women's Beauty Centers",
    "مراكز الحلاقة الرجالية": "Men's Barbershops",
    "مطاعم": "Restaurants",
    "معارض اثاث": "Furniture Showrooms",
    "معارض سيارات": "Car Dealerships",
    "محلات ملابس رجالي نسائي اطفال": "Clothing Stores (Men, Women, Kids)",
    "محلات العطور": "Perfume Shops",
    "مراكز الكوزمتك": "Cosmetic Centers",
    "محلات تجارية": "General Stores",
    "شركات ناشئة": "Startups",
    "شركات العقار": "Real Estate Companies",
    "كافيهات": "Cafes",
    "مجمعات تجارية": "Shopping Malls / Complexes",
    "وكالات": "Agencies",
    "وكالات تبديل زيوت السيارات": "Car Oil Change Stations",
    "مراكز غسل السيارات": "Car Wash Centers",
    "مراكز تصليح السيارات": "Car Repair Centers",
}

# ------------------------------------------------------------------
# Helper: Initialize Headless Chrome Driver
# ------------------------------------------------------------------
def create_driver() -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")

    service = None
    chromedriver_paths = [
        "/usr/bin/chromedriver",
        "/usr/local/bin/chromedriver",
        "/snap/bin/chromium.chromedriver",
        "chromedriver",
    ]
    for path in chromedriver_paths:
        if os.path.isfile(path) or path == "chromedriver":
            try:
                service = Service(path)
                logger.info(f"Using chromedriver at: {path}")
                break
            except Exception:
                continue
    if service is None:
        try:
            service = Service()
        except Exception as exc:
            logger.error(f"Failed to initialize ChromeDriver service: {exc}")
            raise
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver

# ------------------------------------------------------------------
# Helper: Scrape Google Maps (Baghdad only — strict)
# ------------------------------------------------------------------
def scrape_google_maps(query: str, limit: int) -> List[Dict]:
    driver = create_driver()
    results: List[Dict] = []
    try:
        # Strict query: category + location (Arabic + URL-encoded)
        search_term_ar = f"{query} في بغداد، العراق"
        encoded_search = search_term_ar.replace(" ", "+")
        url = f"https://www.google.com/maps/search/{encoded_search}"
        logger.info(f"Strict search term: '{search_term_ar}' | URL: {url}")
        driver.get(url)
        time.sleep(SEARCH_DELAY)

        # Directly submit to Google Maps search input for strict enforcement
        try:
            selectors = [
                "input[name='q']", "input[aria-label*='بحث']",
                "input[placeholder*='بحث']", "#searchboxinput",
                ".searchboxinput", "input.tactile-searchbox-input",
            ]
            for sel in selectors:
                inputs = driver.find_elements(By.CSS_SELECTOR, sel)
                for inp in inputs:
                    if inp.is_displayed():
                        inp.clear()
                        inp.send_keys(search_term_ar)
                        inp.send_keys(Keys.RETURN)
                        logger.info("Directly submitted strict search term.")
                        time.sleep(1.5)
                        break
        except Exception as exc:
            logger.warning(f"Direct input skipped: {exc}")

        scroll_attempts = 0
        max_scrolls = max(3, (limit // 10) + 2)
        while scroll_attempts < max_scrolls:
            soup = BeautifulSoup(driver.page_source, "lxml")
            cards = []
            selectors = [
                "div[role='article']", "div.Nv2PK", "div.bJzME", ".fontHeadlineSmall"
            ]
            for sel in selectors:
                cards.extend(soup.select(sel))

            seen_texts = set()
            unique_cards = []
            for card in cards:
                text_block = card.get_text(separator="|", strip=True)[:120]
                if text_block and text_block not in seen_texts:
                    seen_texts.add(text_block)
                    unique_cards.append(card)

            for card in unique_cards:
                if len(results) >= limit:
                    break
                parsed = extract_card_data(card, query)
                if parsed and parsed.get("name"):
                    exists = any(r.get("name") == parsed.get("name") for r in results)
                    if not exists:
                        # Strict filter: must reference Baghdad/Iraq
                        card_text = (parsed.get("address", "") + " " + parsed.get("name", "")).lower()
                        if any(k in card_text for k in ["بغداد", "baghdad", "العراق", "iraq"]):
                            results.append(parsed)
                            logger.info(f"Extracted (Baghdad verified): {parsed.get('name')}")
                        else:
                            logger.info(f"Filtered out non-Baghdad result: {parsed.get('name')}")
            if len(results) >= limit:
                break
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
            scroll_attempts += 1
    except Exception as exc:
        logger.exception("Scraping error")
        raise
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    return results[:limit]

# ------------------------------------------------------------------
# Helper: Extract data from a card
# ------------------------------------------------------------------
def extract_card_data(card, original_category: str) -> Optional[Dict]:
    try:
        text_all = card.get_text(separator="\n", strip=True)
        lines = [line for line in text_all.splitlines() if line.strip()]

        name_tag = card.select_one("h3, span.fontHeadlineSmall, .fontHeadlineSmall, [aria-label]")
        name = name_tag.get_text(strip=True) if name_tag else (lines[0] if lines else "")
        name = re.sub(r"[⋅··|]+", " ", name).strip()
        if len(name) > 120:
            name = name[:120]

        address = ""
        for line in lines[1:]:
            if len(line) < 2:
                continue
            if any(k in line for k in ["بغداد", "العراق", "Baghdad", "Iraq", "شارع", "حي", "منطقة", "طريق", "Road", "Street", "Ave"]):
                address = line
                break
        if not address and len(lines) > 1:
            for line in lines[1:]:
                if len(line) > 5 and line != name:
                    address = line
                    break

        phone = ""
        phone_patterns = [
            r"\+964\s?[\d\s\-]{6,15}", r"07[\d\s\-]{7,12}", r"[\+\d][\d\s\-]{7,20}"
        ]
        for pattern in phone_patterns:
            match = re.search(pattern, text_all)
            if match:
                phone = re.sub(r"\s+", " ", match.group(0).strip())
                break

        rating = 0.0
        for val_str in re.findall(r"(\d+[.,]\d+)", text_all):
            val = float(val_str.replace(",", "."))
            if 1.0 <= val <= 5.0:
                rating = val
                break

        category = CATEGORIES.get(original_category, original_category)
        if original_category not in CATEGORIES:
            category = original_category

        if not name or name == original_category:
            return None

        return {
            "category": category,
            "name": name,
            "address": address,
            "phone": phone,
            "rating": rating,
            "source_location": LOCATION,
            "original_query": original_category,
        }
    except Exception:
        return None

# ------------------------------------------------------------------
# Endpoint: POST /scrape
# ------------------------------------------------------------------
@app.route("/scrape", methods=["POST"])
def scrape_endpoint():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"status": "error", "message": "Invalid JSON body."}), 400

    category = payload.get("category", "").strip()
    if not category:
        return jsonify({"status": "error", "message": "Missing 'category'."}), 400

    try:
        limit = int(payload.get("limit", DEFAULT_LIMIT))
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    if category not in CATEGORIES:
        logger.warning(f"Unregistered category: '{category}'. Proceeding.")

    logger.info(f"Request | Category: '{category}' | Limit: {limit} | Location: {LOCATION}")

    try:
        data = scrape_google_maps(category, limit)
        return jsonify({
            "status": "success",
            "message": f"Successfully fetched business data for '{category}' in {LOCATION}.",
            "category_requested": category,
            "category_mapped": CATEGORIES.get(category, category),
            "location": LOCATION,
            "requested_limit": limit,
            "results_fetched": len(data),
            "max_limit_design": MAX_LIMIT,
            "note": "Limit set to 52 per request to respect 1000 free-tier budget across 19 categories (~988 total leads).",
            "data": data
        }), 200
    except Exception as exc:
        logger.exception("Scraping endpoint failed")
        return jsonify({
            "status": "error",
            "message": "Scraping operation failed. Ensure Chrome and ChromeDriver are installed.",
            "detail": str(exc),
            "category": category,
            "location": LOCATION,
            "troubleshooting": [
                "Install Chrome + ChromeDriver in deployment environment.",
                "Check category string.",
                "Retry after a brief delay."
            ]
        }), 500

# ------------------------------------------------------------------
# Endpoint: POST /send (WhatsApp / Outreach Webhook)
# ------------------------------------------------------------------
@app.route("/send", methods=["POST"])
def send_endpoint():
    try:
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"status": "error", "message": "Invalid JSON body."}), 400

        phone = str(payload.get("phone") or payload.get("number") or "").strip()
        message = str(payload.get("message") or payload.get("text") or "").strip()

        if not phone:
            logger.error("Error: Phone number is missing!")
            return jsonify({"status": "error", "message": "Phone number is missing"}), 400
        if not message:
            logger.error("Error: Message is missing!")
            return jsonify({"status": "error", "message": "Message is missing"}), 400

        logger.info(f"Received outreach request for phone: {phone}")

        if not WHATSAPP_API_URL or not WHATSAPP_TOKEN:
            logger.error("WhatsApp API configuration is missing.")
            return jsonify({"status": "error", "message": "WhatsApp API is not configured. Set WHATSAPP_API_URL and WHATSAPP_TOKEN."}), 500

        api_payload = {"phone": phone, "message": message}
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

        response = requests.post(WHATSAPP_API_URL, json=api_payload, headers=headers, timeout=30)
        logger.info(f"WhatsApp API Response Status: {response.status_code}")
        logger.info(f"WhatsApp API Response Text: {response.text}")

        if 200 <= response.status_code < 300:
            return jsonify({"status": "success", "phone": phone, "api_status_code": response.status_code, "api_response": response.text}), 200

        return jsonify({"status": "error", "message": "WhatsApp API request failed.", "phone": phone, "api_status_code": response.status_code, "api_response": response.text}), 502

    except requests.RequestException as exc:
        logger.exception("WhatsApp API request failed")
        return jsonify({"status": "error", "message": "Failed to connect to WhatsApp API.", "detail": str(exc)}), 502
    except Exception as exc:
        logger.exception("CRITICAL ERROR in /send")
        return jsonify({"status": "error", "message": "Internal server error.", "detail": str(exc)}), 500


# ------------------------------------------------------------------
# Health Check
# ------------------------------------------------------------------
@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "service": "Google Maps Scraper (Open-Source / Free)",
        "categories_supported": len(CATEGORIES),
        "location_target": LOCATION,
        "default_limit": DEFAULT_LIMIT,
        "max_limit": MAX_LIMIT,
        "endpoints": {
            "POST /scrape": {
                "body_example": {"category": "صيدليات", "limit": 52},
                "description": "Scrape Google Maps business results for a category in Baghdad, Iraq."
            }
        }
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
