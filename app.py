import os
import time
import json
import base64
import hmac
import hashlib
import random
import requests
from flask import Flask, request, jsonify, render_template_string
from playwright.sync_api import sync_playwright

app = Flask(__name__)

# Temporary active session storage
# active_sessions[userKey] = { userKey, dataKey, spinTurnstileToken, mobile }
active_sessions = {}

def fetch_fresh_turnstile():
    print("[ENGINE] Spawning quick headless helper to fetch fresh Turnstile token...")
    token = None
    try:
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
            page.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")
            page.goto("https://discoverworldblends.in/", wait_until="domcontentloaded")
            ts = page.locator("input[name='cf-turnstile-response']")
            for _ in range(25):
                val = ts.input_value()
                if val and len(val) > 20:
                    token = val
                    break
                time.sleep(0.5)
            browser.close()
    except Exception as e:
        print(f"[ENGINE] Fallback Turnstile error: {e}")
    return token

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

# ================= AUTOMATION ENGINE =================
def trigger_campaign_otp(mobile, name="Rahul Sharma", pincode="110001"):
    print(f"[ENGINE] Starting automated Chrome session for {mobile}...")
    captured = {
        "userKey": None,
        "dataKey": None,
        "turnstileToken": None,
        "otpSent": False,
        "error": None
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=-2400,-2400", # keep offscreen
                "--window-size=430,932"
            ]
        )
        context = browser.new_context(
            viewport={"width": 430, "height": 932},
            is_mobile=True,
            user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
        )
        page = context.new_page()
        page.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")

        # Network interceptor to capture keys and confirmation
        def on_res(res):
            try:
                if "/api/collect" in res.url:
                    dec = decode_resp(res.text())
                    captured["userKey"] = dec.get("userKey")
                    captured["dataKey"] = dec.get("dataKey")
                    print(f"[ENGINE] Captured session: userKey={captured['userKey']}")
                elif "/api/users/register/" in res.url:
                    dec = decode_resp(res.text())
                    print(f"[ENGINE] Register API response: {dec}")
                    if dec.get("statusCode") == 200:
                        captured["otpSent"] = True
                    else:
                        captured["error"] = dec.get("message", "Registration rejected")
            except Exception as e:
                pass

        page.on("response", on_res)

        print("[ENGINE] Navigating to https://discoverworldblends.in/...")
        page.goto("https://discoverworldblends.in/", wait_until="load")
        time.sleep(2)

        print(f"[ENGINE] Entering details for {mobile}...")
        page.locator("input[placeholder='Name*']").fill(name)
        page.locator("input[placeholder='Mobile Number*']").fill(mobile)
        pin_input = page.locator("input[placeholder='Pincode*']")
        pin_input.fill(pincode)
        pin_input.press("Tab")
        time.sleep(2) # wait for pincode city/state lookup

        page.locator("label[for='agreeToTnc']").click()
        page.locator("label[for='confirmAge']").click()
        time.sleep(1)

        btn = page.locator("button:has-text('Get Discover Code')")
        ts_elem = page.locator("input[name='cf-turnstile-response']")

        print("[ENGINE] Waiting for Cloudflare Turnstile verification...")
        for i in range(30):
            val = ts_elem.input_value()
            if val and not btn.is_disabled():
                captured["turnstileToken"] = val
                print(f"[ENGINE] Turnstile verified in {i+1}s! Token: {val[:30]}...")
                break
            time.sleep(1)

        if not captured["turnstileToken"]:
            browser.close()
            return {"success": False, "error": "Cloudflare Turnstile verification timeout"}

        print("[ENGINE] Submitting form (clicking Get Discover Code)...")
        try:
            with page.expect_response(lambda res: "/api/users/register/" in res.url, timeout=15000) as response_info:
                btn.click()
            reg_res = response_info.value
            dec = decode_resp(reg_res.text())
            print(f"[ENGINE] Register API direct response: {dec}")
            if dec.get("statusCode") == 200:
                captured["otpSent"] = True
                # Immediately pre-fetch a fresh Turnstile token for the reward wheel!
                print("[ENGINE] Pre-fetching fresh Turnstile token for wheel spin...")
                try:
                    page.evaluate("window.turnstile.reset()")
                    for _ in range(15):
                        new_val = ts_elem.input_value()
                        if new_val and new_val != captured["turnstileToken"]:
                            captured["spinTurnstileToken"] = new_val
                            print(f"[ENGINE] Fresh Spin Turnstile Token ready: {new_val[:30]}...")
                            break
                        time.sleep(0.5)
                except Exception as ex:
                    print(f"[ENGINE] Spin token pre-fetch error: {ex}")
            else:
                captured["error"] = dec.get("message", "Registration rejected")
        except Exception as e:
            print(f"[ENGINE] Submit response error: {e}")
            captured["error"] = "Registration request timed out. Please retry."
        
        time.sleep(1)
        browser.close()

    if captured["otpSent"] and captured["userKey"]:
        session_data = {
            "userKey": captured["userKey"],
            "dataKey": captured["dataKey"],
            "turnstileToken": captured["turnstileToken"],
            "spinTurnstileToken": captured.get("spinTurnstileToken"),
            "mobile": mobile
        }
        active_sessions[str(captured["userKey"])] = session_data
        active_sessions[str(mobile)] = session_data

        return {
            "success": True,
            "userKey": captured["userKey"],
            "dataKey": captured["dataKey"],
            "turnstileToken": captured["turnstileToken"],
            "spinTurnstileToken": captured.get("spinTurnstileToken"),
            "mobile": mobile,
            "message": "OTP sent successfully!"
        }
    else:
        return {
            "success": False,
            "error": captured["error"] or "Failed to dispatch OTP. Please check mobile number."
        }

# ================= FLASK ROUTES =================
@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/request-otp", methods=["POST"])
def api_request_otp():
    try:
        data = request.json or {}
        mobile = str(data.get("mobile", "")).strip()
        name = str(data.get("name", "Rahul Sharma")).strip()
        pincode = str(data.get("pincode", "110001")).strip()

        if not mobile or len(mobile) != 10:
            return jsonify({"success": False, "error": "Please enter a valid 10-digit mobile number"}), 400

        result = trigger_campaign_otp(mobile, name=name, pincode=pincode)
        if result.get("success"):
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        print(f"[API ERROR] {e}")
        return jsonify({"success": False, "error": f"Internal error: {str(e)}"}), 500

@app.route("/api/verify-and-spin", methods=["POST"])
def api_verify_and_spin():
    data = request.json or {}
    user_key = data.get("userKey")
    data_key = data.get("dataKey")
    otp = str(data.get("otp", "")).strip().upper()
    turnstile_token = data.get("turnstileToken", "")
    country = data.get("country", "brazil")

    if not user_key or not data_key or not otp:
        return jsonify({"success": False, "error": "Missing session parameters or OTP"}), 400

    print(f"[API] Verifying OTP {otp} for userKey {user_key}...")

    # 1. Verify OTP
    v_enc = encrypt_payload(user_key, data_key, {"otp": otp})
    v_url = f"https://discoverworldblends.in/api/users/verifyOTP/{user_key}?t={int(time.time()*1000)}"
    v_res = requests.post(v_url, data=v_enc, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    v_data = decode_resp(v_res.text)

    access_token = v_data.get("accessToken")
    if not access_token:
        return jsonify({"success": False, "error": v_data.get("message", "Invalid OTP"), "details": v_data}), 400

    auth_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    # 2. Select Country
    sc_enc = encrypt_payload(user_key, data_key, {"country": country})
    sc_url = f"https://discoverworldblends.in/api/users/selectCountry/{user_key}?t={int(time.time()*1000)}"
    requests.post(sc_url, data=sc_enc, headers=auth_headers, timeout=15)

    # 3. Answer AR Quiz
    ans_enc = encrypt_payload(user_key, data_key, {"answer": "Rich, smooth and aromatic authentic coffee blend with distinct notes"})
    ans_url = f"https://discoverworldblends.in/api/users/answer/{user_key}?t={int(time.time()*1000)}"
    requests.post(ans_url, data=ans_enc, headers=auth_headers, timeout=15)

    # 4. Spin (getReward) with fresh Turnstile token
    session_data = active_sessions.get(str(user_key)) or {}
    spin_token = session_data.get("spinTurnstileToken") or data.get("spinTurnstileToken")
    
    if not spin_token:
        print("[API] Spin token not in memory, fetching fresh Turnstile token...")
        spin_token = fetch_fresh_turnstile()

    if not spin_token:
        spin_token = turnstile_token or "direct_spin_token"

    print(f"[API] Submitting getReward with fresh Turnstile token: {spin_token[:30]}...")
    spin_enc = encrypt_payload(user_key, data_key, {"token": spin_token})
    spin_url = f"https://discoverworldblends.in/api/users/getReward/{user_key}?t={int(time.time()*1000)}"
    spin_res = requests.post(spin_url, data=spin_enc, headers=auth_headers, timeout=15)
    spin_data = decode_resp(spin_res.text)
    print(f"[API] getReward response: {spin_data}")

    # Log won vouchers
    try:
        reward_type = spin_data.get("rewardType", "")
        if spin_data.get("isWinner") or any(k in reward_type.upper() for k in ["SWIGGY", "BOOKMYSHOW", "VOUCHER", "WINNER"]):
            with open("vouchers_won.txt", "a", encoding="utf-8") as vf:
                vf.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] UserKey: {user_key} | Reward: {reward_type} | Data: {json.dumps(spin_data)}\n")
    except Exception:
        pass

    return jsonify({
        "success": True,
        "spinResult": spin_data
    })

# ================= DASHBOARD UI =================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Discover World Blends - Automation Hub</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Plus Jakarta Sans', sans-serif; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col items-center justify-center p-4">

  <div class="w-full max-w-xl bg-slate-900 border border-slate-800 rounded-3xl p-6 md:p-8 shadow-2xl space-y-6">

    <!-- Header -->
    <div class="text-center space-y-2">
      <div class="inline-flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs px-3 py-1 rounded-full font-semibold">
        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
        DISCOVER WORLD BLENDS CAMPAIGN BOT
      </div>
      <h1 class="text-2xl font-bold tracking-tight">Campaign Automation Dashboard</h1>
      <p class="text-xs text-slate-400">नंबर डालें, OTP रिक्वेस्ट करें, और बाकी सारा प्रोसेस (OTP Verify, Answer, Wheel Spin) अपने आप होगा।</p>
    </div>

    <!-- Step 1: Request OTP -->
    <div id="step-phone" class="space-y-4">
      <div>
        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">10-Digit Mobile Number</label>
        <div class="relative">
          <span class="absolute left-4 top-3.5 text-slate-400 text-sm font-semibold">+91</span>
          <input type="tel" id="mobile-input" value="" maxlength="10" placeholder="10-digit number"
            class="w-full pl-14 pr-4 py-3 bg-slate-950 border border-slate-800 rounded-xl text-base font-semibold focus:outline-none focus:border-emerald-500 text-white placeholder-slate-600 transition">
        </div>
      </div>

      <div class="space-y-2">
        <div class="flex items-center justify-between">
          <label class="text-xs font-semibold text-slate-400 uppercase tracking-wider">Profile (Name & Pincode)</label>
          <button type="button" onclick="randomizeProfile()" 
            class="text-[11px] flex items-center gap-1 text-emerald-400 hover:text-emerald-300 bg-emerald-500/10 hover:bg-emerald-500/20 px-2.5 py-1 rounded-lg border border-emerald-500/30 transition active:scale-95">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
            🎲 Randomize (All India)
          </button>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">Random Name</label>
            <input type="text" id="name-input" value="Rahul Sharma"
              class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs font-medium focus:outline-none focus:border-emerald-500 text-white transition">
          </div>
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">Random PIN (India)</label>
            <input type="text" id="pin-input" value="110001" maxlength="6"
              class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs font-medium focus:outline-none focus:border-emerald-500 text-white transition">
          </div>
        </div>
      </div>

      <button id="send-otp-btn" onclick="sendOtp()"
        class="w-full py-3.5 bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 text-slate-950 font-bold rounded-xl transition shadow-lg shadow-emerald-500/20 active:scale-[0.99] flex items-center justify-center gap-2">
        <span id="send-btn-text">Get Discover Code (Send OTP)</span>
        <div id="send-spinner" class="hidden w-4 h-4 border-2 border-slate-950 border-t-transparent rounded-full animate-spin"></div>
      </button>
    </div>

    <!-- Step 2: Enter OTP -->
    <div id="step-otp" class="hidden space-y-4 pt-2 border-t border-slate-800/80">
      <div class="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-xs text-emerald-400">
        ✅ OTP भेजा गया <span id="target-mobile" class="font-bold"></span> पर! 4-अक्षरों वाला कोड नीचे डालें:
      </div>

      <div>
        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">4-Character Code (OTP)</label>
        <input type="text" id="otp-input" maxlength="6" placeholder="उदा. 4D56"
          class="w-full px-4 py-3 bg-slate-950 border border-slate-800 rounded-xl text-center text-2xl font-mono uppercase font-bold tracking-widest focus:outline-none focus:border-emerald-500 text-white transition">
      </div>

      <button id="verify-spin-btn" onclick="verifyAndSpin()"
        class="w-full py-3.5 bg-gradient-to-r from-blue-500 to-indigo-500 hover:from-blue-400 hover:to-indigo-400 text-white font-bold rounded-xl transition shadow-lg shadow-blue-500/20 active:scale-[0.99] flex items-center justify-center gap-2">
        <span id="spin-btn-text">Verify, Answer Quiz & Spin Wheel</span>
        <div id="spin-spinner" class="hidden w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
      </button>
    </div>

    <!-- Step 3: Result -->
    <div id="step-result" class="hidden space-y-4 pt-2 border-t border-slate-800/80">
      <div class="p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-2xl text-center space-y-2">
        <div class="text-3xl">🎉</div>
        <div class="text-lg font-bold text-emerald-400">प्रक्रिया पूरी हुई!</div>
        <div id="result-badge" class="inline-block px-4 py-1.5 bg-amber-500/20 border border-amber-500/30 text-amber-300 text-base font-bold rounded-xl font-mono">
          -
        </div>
        <div id="result-status" class="text-xs text-slate-400 mt-1"></div>
      </div>
      <pre id="result-json" class="p-3 bg-slate-950 rounded-xl text-xs font-mono text-slate-300 overflow-x-auto border border-slate-800 max-h-48"></pre>
      <button onclick="resetFlow()" class="w-full py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold rounded-xl transition">
        Process Another Mobile Number
      </button>
    </div>

    <!-- Terminal Log Stream -->
    <div class="p-3 bg-slate-950 rounded-xl border border-slate-800/80">
      <div class="text-[10px] text-slate-500 uppercase font-mono tracking-wider mb-1">Execution Telemetry</div>
      <div id="log-box" class="text-xs font-mono text-emerald-400/90 space-y-1 h-20 overflow-y-auto">
        <div>[SYSTEM] Ready. Enter mobile number above.</div>
      </div>
    </div>

  </div>

  <script>
    let currentSession = null;

    const RANDOM_PROFILES = [
      { name: "Rahul Sharma", pin: "110001", city: "New Delhi" },
      { name: "Priya Patel", pin: "380001", city: "Ahmedabad" },
      { name: "Amit Verma", pin: "226001", city: "Lucknow" },
      { name: "Sneha Reddy", pin: "500001", city: "Hyderabad" },
      { name: "Vikram Singh", pin: "302001", city: "Jaipur" },
      { name: "Pooja Das", pin: "700001", city: "Kolkata" },
      { name: "Rohan Iyer", pin: "600001", city: "Chennai" },
      { name: "Ananya Deshmukh", pin: "400001", city: "Mumbai" },
      { name: "Deepak Joshi", pin: "411001", city: "Pune" },
      { name: "Suresh Nair", pin: "682001", city: "Kochi" },
      { name: "Kavita Yadav", pin: "800001", city: "Patna" },
      { name: "Manish Mishra", pin: "452001", city: "Indore" },
      { name: "Aakash Jena", pin: "751001", city: "Bhubaneswar" },
      { name: "Gaurav Malhotra", pin: "160017", city: "Chandigarh" },
      { name: "Swati Choudhary", pin: "342001", city: "Jodhpur" },
      { name: "Arjun Sen", pin: "781001", city: "Guwahati" },
      { name: "Megha Gupta", pin: "201301", city: "Noida" },
      { name: "Kunal Mehra", pin: "122001", city: "Gurugram" },
      { name: "Divya Pillai", pin: "695001", city: "Thiruvananthapuram" },
      { name: "Harish Kumar", pin: "560001", city: "Bengaluru" },
      { name: "Rohit Bansal", pin: "141001", city: "Ludhiana" },
      { name: "Sunita Mohanty", pin: "753001", city: "Cuttack" },
      { name: "Aditya Roy", pin: "834001", city: "Ranchi" },
      { name: "Nitin Chauhan", pin: "248001", city: "Dehradun" }
    ];

    function randomizeProfile() {
      const idx = Math.floor(Math.random() * RANDOM_PROFILES.length);
      const chosen = RANDOM_PROFILES[idx];
      document.getElementById("name-input").value = chosen.name;
      document.getElementById("pin-input").value = chosen.pin;
      log(`🎲 Randomized: ${chosen.name} | PIN: ${chosen.pin} (${chosen.city})`);
    }

    // Auto-randomize on page launch
    window.addEventListener("DOMContentLoaded", () => {
      randomizeProfile();
    });

    function log(msg) {
      const b = document.getElementById("log-box");
      const d = document.createElement("div");
      d.innerText = `[${new Date().toLocaleTimeString()}] ${msg}`;
      b.appendChild(d);
      b.scrollTop = b.scrollHeight;
    }

    async function sendOtp() {
      const mobile = document.getElementById("mobile-input").value.trim();
      const name = document.getElementById("name-input").value.trim();
      const pincode = document.getElementById("pin-input").value.trim();

      if (!mobile || mobile.length !== 10) {
        alert("कृपया 10-अंकों का मोबाइल नंबर दर्ज करें।");
        return;
      }

      const btn = document.getElementById("send-otp-btn");
      const text = document.getElementById("send-btn-text");
      const spinner = document.getElementById("send-spinner");
      btn.disabled = true;
      text.innerText = "Requesting OTP (Auto-Solving Security)...";
      spinner.classList.remove("hidden");
      log(`Triggering OTP dispatch for +91 ${mobile}...`);

      try {
        const res = await fetch("/api/request-otp", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mobile, name, pincode })
        });
        const data = await res.json();
        if (data.success) {
          currentSession = data;
          log(`✅ OTP भेजा गया! UserKey: ${data.userKey}`);
          document.getElementById("target-mobile").innerText = mobile;
          document.getElementById("step-otp").classList.remove("hidden");
          document.getElementById("otp-input").focus();
        } else {
          log(`❌ Error: ${data.error || "Failed"}`);
          alert(data.error || "Failed to send OTP");
        }
      } catch (err) {
        log(`❌ Network error: ${err.message}`);
        alert("Error connecting to automation engine.");
      } finally {
        btn.disabled = false;
        text.innerText = "Get Discover Code (Send OTP)";
        spinner.classList.add("hidden");
      }
    }

    async function verifyAndSpin() {
      const otp = document.getElementById("otp-input").value.trim();
      if (!otp) {
        alert("कृपया OTP दर्ज करें।");
        return;
      }

      const btn = document.getElementById("verify-spin-btn");
      const text = document.getElementById("spin-btn-text");
      const spinner = document.getElementById("spin-spinner");
      btn.disabled = true;
      text.innerText = "Verifying, Submitting Answer & Spinning...";
      spinner.classList.remove("hidden");
      log(`Submitting OTP ${otp}...`);

      try {
        const res = await fetch("/api/verify-and-spin", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            userKey: currentSession.userKey,
            dataKey: currentSession.dataKey,
            otp: otp,
            turnstileToken: currentSession.turnstileToken,
            spinTurnstileToken: currentSession.spinTurnstileToken
          })
        });
        const data = await res.json();
        if (data.success) {
          log("🎉 Pipeline Complete! Spin result received.");
          document.getElementById("step-otp").classList.add("hidden");
          document.getElementById("step-result").classList.remove("hidden");
          const r = data.spinResult || {};
          document.getElementById("result-badge").innerText = r.rewardType || "Result Received";
          document.getElementById("result-status").innerText = r.isWinner ? "🏆 Winner!" : "Status: " + (r.message || "Completed");
          document.getElementById("result-json").innerText = JSON.stringify(r, null, 2);
        } else {
          log(`❌ Verification failed: ${data.error}`);
          alert(data.error || "OTP Verification failed");
        }
      } catch (err) {
        log(`❌ Network error: ${err.message}`);
        alert("Network error: " + err.message);
      } finally {
        btn.disabled = false;
        text.innerText = "Verify, Answer Quiz & Spin Wheel";
        spinner.classList.add("hidden");
      }
    }

    function resetFlow() {
      document.getElementById("step-result").classList.add("hidden");
      document.getElementById("step-otp").classList.add("hidden");
      document.getElementById("otp-input").value = "";
      document.getElementById("mobile-input").value = "";
      randomizeProfile();
      log("Ready for next mobile number. Profile randomized.");
    }
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    print("[SERVER] Automation Hub running on http://127.0.0.1:5050")
    app.run(host="0.0.0.0", port=5050)
