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

# In-memory session tracking
active_sessions = {}

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
def trigger_campaign_otp(mobile, name="User", pincode="110001"):
    print(f"[ENGINE] Starting automated Chrome session for +91 {mobile}...")
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

        # Network interceptor to capture assigned userKey and dataKey
        def on_res(res):
            try:
                if "/api/collect" in res.url:
                    dec = decode_resp(res.text())
                    captured["userKey"] = dec.get("userKey")
                    captured["dataKey"] = dec.get("dataKey")
            except Exception:
                pass

        page.on("response", on_res)

        page.goto("https://discoverworldblends.in/", wait_until="load")
        time.sleep(2)

        # Fill inputs
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

        print("[ENGINE] Waiting for Cloudflare Turnstile token...")
        for i in range(30):
            val = ts_elem.input_value()
            if val and not btn.is_disabled():
                captured["turnstileToken"] = val
                print(f"[ENGINE] Turnstile verified in {i+1}s!")
                break
            time.sleep(1)

        if not captured["turnstileToken"]:
            browser.close()
            return {"success": False, "error": "Cloudflare Turnstile verification timeout"}

        print("[ENGINE] Submitting registration form...")
        try:
            with page.expect_response(lambda res: "/api/users/register/" in res.url, timeout=15000) as response_info:
                btn.click()
            reg_res = response_info.value
            dec = decode_resp(reg_res.text())
            print(f"[ENGINE] Register response: {dec}")
            if dec.get("statusCode") == 200:
                captured["otpSent"] = True
            else:
                captured["error"] = dec.get("message", "Registration rejected")
        except Exception as e:
            print(f"[ENGINE] Submit response error: {e}")
            captured["error"] = "Registration request timed out. Please retry."

        time.sleep(1)
        browser.close()

    if captured["otpSent"] and captured["userKey"]:
        active_sessions[mobile] = {
            "userKey": captured["userKey"],
            "dataKey": captured["dataKey"],
            "turnstileToken": captured["turnstileToken"],
            "mobile": mobile
        }
        return {
            "success": True,
            "userKey": captured["userKey"],
            "dataKey": captured["dataKey"],
            "turnstileToken": captured["turnstileToken"],
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
        name = str(data.get("name", "User")).strip()
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
    try:
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

        # 4. Spin (getReward)
        spin_token = turnstile_token if turnstile_token else "direct_spin_token"
        spin_enc = encrypt_payload(user_key, data_key, {"token": spin_token})
        spin_url = f"https://discoverworldblends.in/api/users/getReward/{user_key}?t={int(time.time()*1000)}"
        spin_res = requests.post(spin_url, data=spin_enc, headers=auth_headers, timeout=15)
        spin_data = decode_resp(spin_res.text)

        return jsonify({
            "success": True,
            "spinResult": spin_data
        })
    except Exception as e:
        print(f"[API ERROR] {e}")
        return jsonify({"success": False, "error": f"Verification error: {str(e)}"}), 500

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
      <p class="text-xs text-slate-400">Enter mobile number, request OTP, and the pipeline will automatically verify, answer AR quiz, and spin the wheel.</p>
    </div>

    <!-- Step 1: Request OTP -->
    <div id="step-phone" class="space-y-4">
      <div>
        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">10-Digit Mobile Number</label>
        <div class="relative">
          <span class="absolute left-4 top-3.5 text-slate-400 text-sm font-semibold">+91</span>
          <input type="tel" id="mobile-input" value="" maxlength="10" placeholder="Enter 10-digit number"
            class="w-full pl-14 pr-4 py-3 bg-slate-950 border border-slate-800 rounded-xl text-base font-semibold focus:outline-none focus:border-emerald-500 text-white placeholder-slate-600 transition">
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-xs text-slate-400 mb-1">Name (Optional)</label>
          <input type="text" id="name-input" value="User"
            class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs focus:outline-none focus:border-emerald-500">
        </div>
        <div>
          <label class="block text-xs text-slate-400 mb-1">Pincode</label>
          <input type="text" id="pin-input" value="110001" maxlength="6"
            class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs focus:outline-none focus:border-emerald-500">
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
        ✅ OTP sent to <span id="target-mobile" class="font-bold"></span>! Enter the 4-character code below:
      </div>

      <div>
        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">4-Character Code (OTP)</label>
        <input type="text" id="otp-input" maxlength="6" placeholder="e.g. 4D56"
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
        <div class="text-lg font-bold text-emerald-400">Process Completed!</div>
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
        alert("Please enter a valid 10-digit mobile number.");
        return;
      }

      const btn = document.getElementById("send-otp-btn");
      const text = document.getElementById("send-btn-text");
      const spinner = document.getElementById("send-spinner");
      btn.disabled = true;
      text.innerText = "Requesting OTP (Auto-Solving Captcha)...";
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
          log(`✅ OTP Sent! UserKey: ${data.userKey}`);
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
        alert("Please enter the OTP.");
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
            turnstileToken: currentSession.turnstileToken
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
      log("Ready for next mobile number.");
    }
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"[SERVER] Automation Hub running on http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port)
