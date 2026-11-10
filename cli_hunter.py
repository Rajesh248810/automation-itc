import sys
import os
import re
import time
import json
import base64
import hmac
import hashlib
import random
import requests
from playwright.sync_api import sync_playwright

# ================= CRYPTO & API HELPERS =================
def encrypt_payload(user_key, data_key, payload_obj):
    timestamp = int(time.time() * 1000)
    payload_obj["userKey"] = user_key
    payload_obj["t"] = timestamp

    json_str = json.dumps(payload_obj, separators=(',', ':'))
    a = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
    u = base64.b64encode(str(timestamp).encode('utf-8')).decode('utf-8')

    key = data_key[4:18] # substr(4, 14)
    message = f"{u}.{a}"

    hmac_hex = hmac.new(key.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
    f = base64.b64encode(hmac_hex.encode('utf-8')).decode('utf-8')

    p = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    m = random.randint(1, 6)
    k = random.randint(2, 8)
    noise = "".join(random.choice(p) for _ in range(k))

    s = f"{k}{m}{f[:m]}{noise}{f[m:]}"
    g = f"{u}.{a}.{s}"

    return {
        "userKey": str(user_key),
        "data": g
    }

def decode_resp(text):
    try:
        j = json.loads(text)
        if "resp" in j:
            return json.loads(base64.b64decode(j["resp"]).decode('utf-8'))
        return j
    except Exception:
        return {"raw": text}

def extract_otp(raw_text):
    """
    Extracts 4-character alphanumeric OTP even if user pastes the entire SMS.
    Sample: 'Hi, Your Code for Discover World Blends Promo is VMBV . Please do not share...'
    """
    raw_text = raw_text.strip()
    if len(raw_text) == 4 and raw_text.isalnum():
        return raw_text.upper()
    
    patterns = [
        r"Promo is\s+([A-Za-z0-9]{4})",
        r"is\s+([A-Za-z0-9]{4})\s*\.",
        r"Code[:\s]+([A-Za-z0-9]{4})",
        r"\b([A-Za-z0-9]{4})\b"
    ]
    for p in patterns:
        m = re.search(p, raw_text, re.IGNORECASE)
        if m:
            return m.group(1).upper()
            
    return raw_text[:4].upper()

# ================= AUTOMATION PIPELINE =================
def run_campaign_flow(mobile, name="Player", pincode="110001"):
    print("\n" + "="*60)
    print(f"🚀 [START] Processing Mobile: +91 {mobile}")
    print("="*60)

    captured = {
        "userKey": None,
        "dataKey": None,
        "turnstileToken": None,
        "otpSent": False,
        "error": None
    }

    print("🛡️ [1/4] Spawning Chrome engine to solve Cloudflare Turnstile...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=-2400,-2400",
                "--window-size=430,932"
            ]
        )
        context = browser.new_context(
            viewport={"width": 430, "height": 932},
            is_mobile=True,
            user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
        )
        page = context.new_page()

        def handle_response(response):
            if "/api/users/register/" in response.url:
                try:
                    body = response.text()
                    data = decode_resp(body)
                    if "userKey" in data:
                        captured["userKey"] = data["userKey"]
                    if "dataKey" in data:
                        captured["dataKey"] = data["dataKey"]
                    if data.get("statusCode") == 200:
                        captured["otpSent"] = True
                    elif "message" in data:
                        captured["error"] = data["message"]
                except Exception as e:
                    pass

        page.on("response", handle_response)

        try:
            page.goto("https://discoverworldblends.in/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # Age verification
            age_btn = page.locator("button:has-text('Yes, I am')")
            if age_btn.count() > 0 and age_btn.is_visible():
                age_btn.click()
                page.wait_for_timeout(1000)

            # Accept cookies if any
            accept_btn = page.locator("button:has-text('Accept')")
            if accept_btn.count() > 0 and accept_btn.is_visible():
                accept_btn.click()

            # Fill Form
            page.fill("input[name='name']", name)
            page.fill("input[name='mobile']", str(mobile))
            page.fill("input[name='pincode']", str(pincode))

            # Trigger state/city lookup
            page.press("input[name='pincode']", "Tab")
            page.wait_for_timeout(1500)

            # Check Terms
            terms_chk = page.locator("input[type='checkbox']").first
            if terms_chk.count() > 0 and not terms_chk.is_checked():
                terms_chk.check(force=True)

            print("⏳ Waiting for Turnstile token resolution...")
            for _ in range(30):
                ts_elem = page.locator("input[name='cf-turnstile-response']")
                btn = page.locator("button:has-text('Get Discover Code')")
                if ts_elem.count() > 0:
                    val = ts_elem.input_value()
                    if val and len(val) > 10 and not btn.is_disabled():
                        captured["turnstileToken"] = val
                        break
                page.wait_for_timeout(500)

            if not captured["turnstileToken"]:
                print("❌ Turnstile token resolution timed out.")
                browser.close()
                return False

            print("✅ Turnstile verified! Requesting OTP...")
            page.locator("button:has-text('Get Discover Code')").click()

            for _ in range(20):
                if captured["otpSent"] or captured["error"]:
                    break
                page.wait_for_timeout(500)

            browser.close()

        except Exception as e:
            print(f"❌ Automation Error: {e}")
            browser.close()
            return False

    if not captured["otpSent"]:
        err_msg = captured.get("error") or "Unknown error requesting OTP"
        print(f"❌ Failed to send OTP: {err_msg}")
        return False

    print("📲 [2/4] OTP dispatched successfully to mobile!")
    user_key = captured["userKey"]
    data_key = captured["dataKey"]

    # Prompt User for OTP
    raw_otp = input("\n👉 Enter 4-character OTP (or paste full SMS): ").strip()
    otp = extract_otp(raw_otp)
    print(f"🔑 Using extracted OTP: [{otp}]")

    # ================= PURE API EXECUTION =================
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
        "Origin": "https://discoverworldblends.in",
        "Referer": "https://discoverworldblends.in/",
        "Content-Type": "application/json"
    })

    print("\n⚡ [3/4] Verifying OTP & skipping quiz via APIs...")
    # 1. Verify OTP
    verify_payload = encrypt_payload(user_key, data_key, {"otp": otp})
    r_ver = sess.post("https://discoverworldblends.in/api/users/verifyOtp/", json=verify_payload)
    ver_res = decode_resp(r_ver.text)

    if ver_res.get("statusCode") != 200:
        print(f"❌ OTP Verification Failed: {ver_res.get('message', r_ver.text)}")
        return False
    print("✅ OTP Verified successfully!")

    # 2. Select Country (1 = USA)
    country_payload = encrypt_payload(user_key, data_key, {"countryId": 1})
    sess.post("https://discoverworldblends.in/api/users/updateCountry/", json=country_payload)

    # 3. Answer Quiz / AR
    ar_payload = encrypt_payload(user_key, data_key, {"questionId": 1, "answer": "Grand Canyon"})
    sess.post("https://discoverworldblends.in/api/users/updateAnswer/", json=ar_payload)
    print("✅ Country & Quiz skipped in 0.2s!")

    # 4. Trigger Spin & Win
    print("\n🎰 [4/4] Triggering Reward Spin...")
    spin_payload = encrypt_payload(user_key, data_key, {})
    r_spin = sess.post("https://discoverworldblends.in/api/users/getReward/", json=spin_payload)
    spin_res = decode_resp(r_spin.text)

    print("="*60)
    print("🎉 CAMPAIGN COMPLETED SUCCESSFULLY!")
    print("="*60)
    print("Result Payload:")
    print(json.dumps(spin_res, indent=2))

    # Save to file
    with open("rewards_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Mobile: {mobile} | Reward: {json.dumps(spin_res)}\n")
    print("\n💾 Result logged to rewards_log.txt")
    return True

if __name__ == "__main__":
    print("╔════════════════════════════════════════════════════════════╗")
    print("║   ITC Discover World Blends - PC CLI Automation Hunter     ║")
    print("╚════════════════════════════════════════════════════════════╝")
    
    mobile = input("Enter 10-digit mobile number: ").strip()
    if len(mobile) == 10 and mobile.isdigit():
        run_campaign_flow(mobile)
    else:
        print("❌ Invalid 10-digit mobile number.")
