# ITC Discover World Blends Automation Suite

A fast, automated solution for the **ITC Discover World Blends** promotional campaign.

This suite provides two interfaces:
1. **Interactive Web Dashboard (`app.py`)**: A modern, mobile-responsive web UI for one-click OTP dispatch, clipboard/SMS OTP submission, and instant spin reward results.
2. **Terminal CLI Hunter (`cli_hunter.py`)**: A lightweight command-line script for fast terminal execution.

---

## ✨ Features

- **Automated Cloudflare Turnstile Resolution**: Seamlessly solves Turnstile challenges using off-screen Chrome context.
- **Pure API Fast-Forward**: Skips country selection, 360° AR panoramas, and quizzes directly via encrypted REST APIs in < 0.5s.
- **Smart SMS / OTP Parser**: Automatically extracts 4-character alphanumeric codes (`[A-Za-z0-9]{4}`) even when pasting the entire SMS.
- **Dual Execution Modes**: Choose between the visual Flask web dashboard or the fast CLI tool.
- **Instant Voucher Logging**: Saves winning rewards, voucher codes, and timestamps locally.

---

## 🛠️ Prerequisites

- **Python 3.9+**
- **Google Chrome** installed on your system

---

## 📦 Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Rajesh248810/automation-itc.git
   cd automation-itc
   ```

2. **Install required Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright browsers**:
   ```bash
   playwright install chromium
   ```

---

## 🚀 Usage

### Option 1: Web Dashboard (Recommended)

1. Run the Flask server:
   ```bash
   python app.py
   ```
2. Open your browser and navigate to:
   ```
   http://127.0.0.1:5050
   ```
3. Enter the 10-digit mobile number, click **"Send OTP & Bypass"**, enter the OTP received via SMS, and view your winning reward instantly.

---

### Option 2: Terminal CLI Tool

1. Run the CLI script:
   ```bash
   python cli_hunter.py
   ```
2. Enter the mobile number when prompted.
3. Enter or paste the SMS OTP to complete the flow.

---

## ⚙️ How It Works

1. **Turnstile Resolution**: Uses Playwright with stealth flags to render the campaign page offscreen and retrieve a valid `cf-turnstile-response` token.
2. **Registration API**: Dispatches OTP to the user's mobile number and extracts session `userKey` and `dataKey`.
3. **Encrypted Execution**: Uses the campaign's custom HMAC-SHA256 payload encoding logic to execute OTP verification, country selection, and reward spins over HTTP requests.

---

## 📄 License

This repository is for educational and research purposes only.
